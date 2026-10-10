"""Tests for the MirAIe climate entity.

The transport is faked by the shared conftest fixture, so these tests never open a
socket and never touch the physical AC. They assert on the frames the entity asks
the transport to send, which is where the protocol mapping lives.
"""

from __future__ import annotations

from conftest import CLIMATE_ENTITY_ID, STATUS, sent


async def test_attributes(hass, setup_entry):
    """Modes, fan speeds, temperature bounds and the reported state."""
    state = hass.states.get(CLIMATE_ENTITY_ID)

    assert state.attributes["hvac_modes"] == ["off", "cool", "dry", "fan_only", "auto"]
    assert state.attributes["fan_modes"] == ["auto", "quiet", "low", "medium", "high"]
    assert state.attributes["min_temp"] == 16.0
    assert state.attributes["max_temp"] == 30.0
    assert state.attributes["target_temp_step"] == 0.5
    assert state.attributes["current_temperature"] == 26.5  # rmtmp
    assert state.attributes["temperature"] == 24.0  # actmp
    assert state.state == "off"  # ps off
    # HA 2026.10 exposes no temperature_unit state attribute (2026.11 adds it),
    # so check the feature flags that gate the turn_on/turn_off services instead.
    assert state.attributes["supported_features"] & 128  # TURN_ON
    assert state.attributes["supported_features"] & 256  # TURN_OFF


async def test_set_temperature_sends_frame(hass, setup_entry, transport):
    """Setting a temperature also powers the unit on, in the same frame (D12)."""
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": CLIMATE_ENTITY_ID, "temperature": 25.0},
        blocking=True,
    )

    assert sent(transport)[-1] == {"ki": 2, "actmp": "25.0", "ps": "on"}


async def test_set_temperature_while_off_powers_on(hass, setup_entry, transport):
    """The unit is off; setting a value must still turn it on (D12)."""
    assert hass.states.get(CLIMATE_ENTITY_ID).state == "off"

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": CLIMATE_ENTITY_ID, "temperature": 25.5},
        blocking=True,
    )

    frame = sent(transport)[-1]
    assert frame["ps"] == "on"
    assert frame["actmp"] == "25.5"


async def test_turn_off_sends_ps_off(hass, setup_entry, transport):
    await hass.services.async_call(
        "climate", "turn_off", {"entity_id": CLIMATE_ENTITY_ID}, blocking=True
    )

    assert sent(transport)[-1] == {"ki": 1, "ps": "off"}


async def test_turn_on_sends_cooling(hass, setup_entry, transport):
    """turn_on must not ask for heat_cool: this unit has no heat mode."""
    await hass.services.async_call(
        "climate", "turn_on", {"entity_id": CLIMATE_ENTITY_ID}, blocking=True
    )

    assert sent(transport)[-1] == {"ki": 3, "acmd": "cool", "ps": "on"}


async def test_set_hvac_mode_fan_only_maps_to_the_firmware_fan(hass, setup_entry, transport):
    """Home Assistant spells it fan_only, the firmware spells it fan."""
    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": CLIMATE_ENTITY_ID, "hvac_mode": "fan_only"},
        blocking=True,
    )

    assert sent(transport)[-1] == {"ki": 3, "acmd": "fan", "ps": "on"}


async def test_set_hvac_mode_auto_maps_to_auto(hass, setup_entry, transport):
    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": CLIMATE_ENTITY_ID, "hvac_mode": "auto"},
        blocking=True,
    )

    assert sent(transport)[-1] == {"ki": 3, "acmd": "auto", "ps": "on"}


async def test_set_fan_mode_sends_frame(hass, setup_entry, transport):
    await hass.services.async_call(
        "climate",
        "set_fan_mode",
        {"entity_id": CLIMATE_ENTITY_ID, "fan_mode": "low"},
        blocking=True,
    )

    assert sent(transport)[-1] == {"ki": 4, "acfs": "low", "ps": "on"}


async def test_heat_is_not_offered(hass, setup_entry):
    """The unit is not a heating model, so heat must not appear."""
    modes = hass.states.get(CLIMATE_ENTITY_ID).attributes["hvac_modes"]

    assert "heat" not in modes
    assert "heat_cool" not in modes


async def test_a_broadcast_updates_the_entity(hass, setup_entry):
    """The entity is push-driven: a broadcast updates its state."""
    setup_entry.runtime_data.handle_status(
        {**STATUS, "ps": "on", "actmp": "22.0", "acmd": "dry", "acfs": "high"}
    )
    await hass.async_block_till_done()

    state = hass.states.get(CLIMATE_ENTITY_ID)
    assert state.state == "dry"
    assert state.attributes["temperature"] == 22.0
    assert state.attributes["fan_mode"] == "high"


async def test_unknown_room_temperature_is_not_reported(hass, setup_entry):
    """The unit reports 00.0 for a room temperature it does not know."""
    setup_entry.runtime_data.handle_status({**STATUS, "rmtmp": "00.0"})
    await hass.async_block_till_done()

    assert hass.states.get(CLIMATE_ENTITY_ID).attributes["current_temperature"] is None


async def test_unavailable_when_the_coordinator_fails(hass, setup_entry):
    """When the AC stops answering, the entity becomes unavailable."""
    coordinator = setup_entry.runtime_data
    coordinator.last_update_success = False
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert hass.states.get(CLIMATE_ENTITY_ID).state == "unavailable"


async def test_set_temperature_without_a_value_sends_nothing(hass, setup_entry, transport):
    """A set_temperature with no temperature is a no-op."""
    entity = hass.data["entity_components"]["climate"].get_entity(CLIMATE_ENTITY_ID)

    await entity.async_set_temperature()
    await hass.async_block_till_done()

    assert sent(transport) == []


async def test_set_temperature_survives_a_dead_transport(hass, setup_entry, transport):
    """With no transport the entity logs instead of raising into HA."""
    setup_entry.runtime_data.transport = None

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": CLIMATE_ENTITY_ID, "temperature": 25.0},
        blocking=True,
    )

    assert sent(transport) == []
