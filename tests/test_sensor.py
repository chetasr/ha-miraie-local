"""Tests for the MirAIe diagnostic sensors.

The transport is faked by the shared conftest fixture, so these tests never open a
socket and never touch the physical AC.
"""

from __future__ import annotations

from conftest import (
    ROOM_TEMP_ENTITY_ID,
    RSSI_ENTITY_ID,
    STATUS,
    VERSION_ENTITY_ID,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory


async def test_rssi_sensor(hass, setup_entry):
    state = hass.states.get(RSSI_ENTITY_ID)

    assert state.state == "-51"
    assert state.attributes["unit_of_measurement"] == "dBm"
    assert state.attributes["device_class"] == "signal_strength"


async def test_room_temperature_sensor(hass, setup_entry):
    state = hass.states.get(ROOM_TEMP_ENTITY_ID)

    assert state.state == "26.5"
    assert state.attributes["unit_of_measurement"] == "°C"
    assert state.attributes["device_class"] == "temperature"


async def test_firmware_version_sensor(hass, setup_entry):
    state = hass.states.get(VERSION_ENTITY_ID)

    assert state.state == "3.02"


async def test_sensors_are_diagnostic(hass, setup_entry):
    """entity_category is registry-only, so read it from the registry."""
    registry = er.async_get(hass)

    for entity_id in (RSSI_ENTITY_ID, ROOM_TEMP_ENTITY_ID, VERSION_ENTITY_ID):
        assert hass.states.get(entity_id) is not None, entity_id
        entry = registry.async_get(entity_id)
        assert entry is not None, entity_id
        assert entry.entity_category is EntityCategory.DIAGNOSTIC, entity_id


async def test_sensors_share_the_device_of_the_climate_entity(hass, setup_entry):
    """All entities must group under one device."""
    climate = hass.states.get("climate.panasonic_ac")
    rssi = hass.states.get(RSSI_ENTITY_ID)

    assert climate.attributes["friendly_name"] == "Panasonic AC"
    assert rssi.attributes["friendly_name"] == "Panasonic AC RSSI"


async def test_sensors_update_from_a_broadcast(hass, setup_entry):
    """The sensors are pushed from the same status broadcast."""
    setup_entry.runtime_data.handle_status({**STATUS, "rssi": -60, "rmtmp": "27.0"})
    await hass.async_block_till_done()

    assert hass.states.get(RSSI_ENTITY_ID).state == "-60"
    assert hass.states.get(ROOM_TEMP_ENTITY_ID).state == "27.0"


async def test_unknown_room_temperature_is_not_zero(hass, setup_entry):
    """The unit reports 00.0 for a room temperature it does not know."""
    setup_entry.runtime_data.handle_status({**STATUS, "rmtmp": "00.0"})
    await hass.async_block_till_done()

    assert hass.states.get(ROOM_TEMP_ENTITY_ID).state == "unknown"


async def test_missing_fields_are_unknown(hass, setup_entry):
    """A broadcast that omits a field leaves that sensor unknown, not stale."""
    setup_entry.runtime_data.handle_status({"ps": "on"})
    await hass.async_block_till_done()

    assert hass.states.get(RSSI_ENTITY_ID).state == "unknown"
    assert hass.states.get(VERSION_ENTITY_ID).state == "unknown"
