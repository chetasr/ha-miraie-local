"""Shared test fixtures for the MirAIe Local integration.

The transport is faked everywhere, so no test opens a socket or touches the
physical AC. The AES key/IV used in tests are SYNTHETIC and must never be
replaced with the real ``udpControlKey``/``udpControlIV``.
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

pytest_plugins = ["pytest_homeassistant_custom_component"]

DEVICE_ID = "8c1419857fd4"
HOST = "10.10.1.125"
KEY_HEX = "ab" * 32  # synthetic
IV_HEX = "cd" * 16  # synthetic

# The unit is off, targeting 24.0 in a 26.5 room, in cool/auto.
STATUS = {
    "ts": "1791640218",
    "rssi": -51,
    "ps": "off",
    "actmp": "24.0",
    "rmtmp": "26.5",
    "acmd": "cool",
    "acfs": "auto",
    "V": "3.02",
}

CLIMATE_ENTITY_ID = "climate.panasonic_ac"
RSSI_ENTITY_ID = "sensor.panasonic_ac_rssi"
ROOM_TEMP_ENTITY_ID = "sensor.panasonic_ac_room_temperature"
VERSION_ENTITY_ID = "sensor.panasonic_ac_firmware_version"


class FakeTransport:
    """Records the payloads the entity sends; no socket is involved."""

    instances: list[FakeTransport] = []

    def __init__(self, *args, **kwargs):
        self.on_status = kwargs.get("on_status")
        self.sent: list[dict] = []
        self.started = False
        self.stopped = False
        FakeTransport.instances.append(self)

    async def async_start(self):
        self.started = True

    async def async_stop(self):
        self.stopped = True

    async def async_query(self):
        return dict(STATUS)

    async def async_send(self, payload):
        self.sent.append(payload)


@pytest.fixture
def transport(monkeypatch):
    """Replace the transport the integration builds, recording its payloads."""
    FakeTransport.instances = []
    monkeypatch.setattr(
        init_module, "MirAIeLocalTransport", lambda *a, **k: FakeTransport(*a, **k)
    )
    return FakeTransport


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant, enable_custom_integrations, transport
) -> MockConfigEntry:
    """Set the integration up with the fake transport and return the entry."""
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
    """The payloads the single fake transport instance has been asked to send."""
    return transport.instances[0].sent
