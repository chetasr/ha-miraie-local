"""Tests for the MirAIe climate entity.

The transport is faked, so these tests never open a socket and never touch the
physical AC. The assertions are on the frames the entity asks the transport to send,
which is where the protocol mapping actually lives.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.miraie_local as init_module
from custom_components.miraie_local.const import (
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_IV,
    CONF_KEY,
    DOMAIN,
)

DEVICE_ID = "8c1419857fd4"
HOST = "10.10.1.125"
# Synthetic AES material - never the real udpControlKey/udpControlIV.
KEY_HEX = "ab" * 32
IV_HEX = "cd" * 16

ENTITY_ID = "climate.panasonic_ac"

# The AC is off, targeting 24.0 in a 26.5 room, in cool/auto.
STATUS = {
    "ts": "1791640218",
    "rssi": -50,
    "ps": "off",
    "actmp": "24.0",
    "rmtmp": "26.5",
    "acmd": "cool",
    "acfs": "auto",
    "V": "3.02",
}


class FakeTransport:
    """Records the payloads the entity sends; no socket is involved."""

    instances: list[FakeTransport] = []

    def __init__(self, *args, **kwargs):
        self.on_status = kwargs.get("on_status")
        self.sent: list[dict] = []
        self.stopped = False
        FakeTransport.instances.append(self)

    async def async_start(self):
        return None

    async def async_stop(self):
        self.stopped = True

    async def async_query(self):
        return dict(STATUS)

    async def async_send(self, payload):
        self.sent.append(payload)


@pytest.fixture
def transport(monkeypatch):
    FakeTransport.instances = []
    monkeypatch.setattr(
        init_module, "MirAIeLocalTransport", lambda *a, **k: FakeTransport(*a, **k)
    )
    return FakeTransport


@pytest.fixture
async def setup_entry(hass: HomeAssistant, enable_custom_integrations, transport):
    """Set the integration up with a fake transport and return the entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: HOST,
            CONF_DEVICE_ID: DEVICE_ID,
            CONF_KEY: KEY_HEX,
            CONF_IV: IV_HEX,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def sent(transport) -> list[dict]:
    return transport.instances[0].sent


async def test_attributes(hass, setup_entry, transport):
    """Modes, fan speeds, temperature bounds and the reported state."""
    state = hass.states.get(ENTITY_ID)

    assert state.attributes["hvac_modes"] == ["off", "cool", "dry", "fan_only", "auto"]
    assert state.attributes["fan_modes"] == ["auto", "quiet", "low", "medium", "high"]
    assert state.attributes["min_temp"] == 16.0
    assert state.attributes["max_temp"] == 30.0
    assert state.attributes["target_temp_step"] == 0.5
    assert state.attributes["current_temperature"] == 26.5  # rmtmp
    assert state.attributes["temperature"] == 24.0  # actmp
    # HA 2026.10 exposes no temperature_unit state attribute (2026.11 adds it).
    assert state.attributes["supported_features"] & 128  # TURN_ON
    assert state.attributes["supported_features"] & 256  # TURN_OFF
    assert state.state == "off"  # ps off


async def test_entity_is_named_after_the_device(hass, setup_entry):
    assert hass.states.get(ENTITY_ID) is not None


async def test_set_temperature_sends_frame(hass, setup_entry, transport):
    """Setting a temperature also powers the unit on, in the same frame (D12)."""
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": ENTITY_ID, "temperature": 25.0},
        blocking=True,
    )

    assert sent(transport)[-1] == {"ki": 2, "actmp": "25.0", "ps": "on"}


async def test_set_temperature_while_off_powers_on(hass, setup_entry, transport):
    """The unit is off; setting a value must still turn it on (D12)."""
    assert hass.states.get(ENTITY_ID).state == "off"

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": ENTITY_ID, "temperature": 25.5},
        blocking=True,
    )

    frame = sent(transport)[-1]
    assert frame["ps"] == "on"
    assert frame["actmp"] == "25.5"


async def test_turn_off_sends_ps_off(hass, setup_entry, transport):
    await hass.services.async_call(
        "climate", "turn_off", {"entity_id": ENTITY_ID}, blocking=True
    )

    assert sent(transport)[-1] == {"ki": 1, "ps": "off"}


async def test_turn_on_sends_a_mode(hass, setup_entry, transport):
    """turn_on has no mode of its own, so it turns cooling on."""
    await hass.services.async_call(
        "climate", "turn_on", {"entity_id": ENTITY_ID}, blocking=True
    )

    assert sent(transport)[-1] == {"ki": 3, "acmd": "cool", "ps": "on"}


async def test_set_hvac_mode_fan_only_maps_to_the_firmware_fan(
    hass, setup_entry, transport
):
    """HA spells it fan_only, the firmware spells it fan."""
    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": ENTITY_ID, "hvac_mode": "fan_only"},
        blocking=True,
    )

    assert sent(transport)[-1] == {"ki": 3, "acmd": "fan", "ps": "on"}


async def test_set_hvac_mode_auto_maps_to_auto(hass, setup_entry, transport):
    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": ENTITY_ID, "hvac_mode": "auto"},
        blocking=True,
    )

    assert sent(transport)[-1] == {"ki": 3, "acmd": "auto", "ps": "on"}


async def test_set_fan_mode_sends_frame(hass, setup_entry, transport):
    await hass.services.async_call(
        "climate",
        "set_fan_mode",
        {"entity_id": ENTITY_ID, "fan_mode": "low"},
        blocking=True,
    )

    assert sent(transport)[-1] == {"ki": 4, "acfs": "low", "ps": "on"}


async def test_heat_is_not_offered(hass, setup_entry):
    """The unit is not a heating model, so heat must not appear."""
    state = hass.states.get(ENTITY_ID)

    assert "heat" not in state.attributes["hvac_modes"]
    assert "heat_cool" not in state.attributes["hvac_modes"]


async def test_a_broadcast_updates_the_entity(hass, setup_entry, transport):
    """The entity is push-driven: a broadcast updates its state."""
    entry = setup_entry
    entry.runtime_data.handle_status(
        {**STATUS, "ps": "on", "actmp": "22.0", "acmd": "dry", "acfs": "high"}
    )
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state.state == "dry"
    assert state.attributes["temperature"] == 22.0
    assert state.attributes["fan_mode"] == "high"


async def test_unknown_room_temperature_is_not_reported(hass, setup_entry):
    """The unit reports 00.0 for a room temperature it does not know."""
    entry = setup_entry
    entry.runtime_data.handle_status({**STATUS, "rmtmp": "00.0"})
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).attributes["current_temperature"] is None


async def test_unavailable_when_the_coordinator_fails(hass, setup_entry, transport):
    """When the AC stops answering, the entity becomes unavailable."""
    entry = setup_entry
    coordinator = entry.runtime_data
    coordinator.last_update_success = False
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == "unavailable"


async def test_set_temperature_without_a_value_sends_nothing(hass, setup_entry, transport):
    """A set_temperature with no temperature is a no-op (HA rejects the call)."""
    entity = hass.data["entity_components"]["climate"].get_entity(ENTITY_ID)

    await entity.async_set_temperature()
    await hass.async_block_till_done()

    assert sent(transport) == []


async def test_set_temperature_survives_a_dead_transport(hass, setup_entry, transport):
    """With no transport the entity logs instead of raising into HA."""
    entry = setup_entry
    entry.runtime_data.transport = None

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": ENTITY_ID, "temperature": 25.0},
        blocking=True,
    )

    assert sent(transport) == []
