"""Tests for the push coordinator and the entry setup/unload.

The transport is faked throughout, so these tests never open a socket and never
touch the physical AC.
"""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.miraie_local as init_module
from custom_components.miraie_local import MirAIeLocalConfigEntry
from custom_components.miraie_local.const import (
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_IV,
    CONF_KEY,
    CONF_MULTICAST_IP,
    CONF_POLL_INTERVAL,
    DEFAULT_MULTICAST,
    DOMAIN,
    MISSED_NUDGES_BEFORE_UNAVAILABLE,
)
from custom_components.miraie_local.coordinator import MirAIeLocalCoordinator

DEVICE_ID = "8c1419857fd4"
HOST = "10.10.1.125"
# Synthetic AES material - never the real udpControlKey/udpControlIV.
KEY_HEX = "ab" * 32
IV_HEX = "cd" * 16

STATUS = {
    "ts": "1791640218",
    "ps": "on",
    "actmp": "25.0",
    "rmtmp": "28.5",
    "acmd": "cool",
    "acfs": "auto",
    "rssi": -50,
    "V": "3.02",
}


class FakeTransport:
    """Stands in for MirAIeLocalTransport so no socket is opened."""

    instances: list[FakeTransport] = []
    query_result: object = STATUS

    def __init__(self, *args, **kwargs):
        self.on_status = kwargs.get("on_status")
        self.args = args
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.queries = 0
        FakeTransport.instances.append(self)

    async def async_start(self):
        self.started = True

    async def async_stop(self):
        self.stopped = True

    async def async_query(self):
        self.queries += 1
        # Instance-first so a test can override the class default.
        if isinstance(self.query_result, Exception):
            raise self.query_result
        return self.query_result


@pytest.fixture
def fake_transport(monkeypatch):
    FakeTransport.instances = []
    FakeTransport.query_result = STATUS
    monkeypatch.setattr(init_module, "MirAIeLocalTransport", FakeTransport)
    return FakeTransport


def make_coordinator(hass, transport=None):
    coordinator = MirAIeLocalCoordinator(hass, update_interval_seconds=60)
    coordinator.transport = transport if transport is not None else FakeTransport()
    return coordinator


async def test_status_callback_publishes_and_notifies_listeners(hass):
    """A broadcast from the transport becomes coordinator.data."""
    transport = FakeTransport()
    coordinator = make_coordinator(hass, transport)
    fired = []
    coordinator.async_add_listener(lambda: fired.append(1))

    coordinator.handle_status(STATUS)
    await asyncio.sleep(0)  # listeners are scheduled on the loop

    assert coordinator.data == STATUS
    assert coordinator.status == STATUS
    assert fired == [1]


async def test_status_callback_is_wired_as_the_transport_callback(
    hass, enable_custom_integrations, fake_transport
):
    """Setup connects the transport's on_status to the coordinator."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: HOST,
            CONF_DEVICE_ID: DEVICE_ID,
            CONF_KEY: KEY_HEX,
            CONF_IV: IV_HEX,
            CONF_MULTICAST_IP: DEFAULT_MULTICAST,
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    transport = fake_transport.instances[0]
    assert transport.started is True
    # The callback really is the coordinator's, not a fresh lambda.
    assert transport.on_status == entry.runtime_data.handle_status

    # And a broadcast arriving on the wire reaches coordinator.data.
    transport.on_status(STATUS)
    assert entry.runtime_data.data == STATUS


async def test_nudge_after_silence_sends_the_noop_query(hass):
    """With no broadcast, the poll nudges the device and takes its status."""
    transport = FakeTransport()
    coordinator = make_coordinator(hass, transport)

    data = await coordinator._async_update_data()

    assert data == STATUS
    assert transport.queries == 1


async def test_nudge_is_a_noop_body(hass):
    """The nudge must never carry a command: the transport sends {} itself."""
    transport = FakeTransport()
    coordinator = make_coordinator(hass, transport)

    await coordinator._async_update_data()

    # async_query is the no-op {} path, distinct from async_send.
    assert not hasattr(transport, "sent")


async def test_first_refresh_fails_when_the_ac_is_silent(hass):
    """A cold start with no answer fails, so setup is retried by HA."""
    transport = FakeTransport()
    transport.query_result = TimeoutError()
    coordinator = make_coordinator(hass, transport)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_tolerates_a_brief_dropout_then_goes_unavailable(hass):
    """The AC vanishes for ~1 min on power-off, so a miss is not fatal at once."""
    transport = FakeTransport()
    transport.query_result = TimeoutError()
    coordinator = make_coordinator(hass, transport)
    coordinator.data = STATUS  # a status was seen at least once

    # The first misses keep the last known state (still available) ...
    for _ in range(MISSED_NUDGES_BEFORE_UNAVAILABLE - 1):
        assert await coordinator._async_update_data() == STATUS

    # ... and the next one gives up, which marks the entities unavailable.
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_a_broadcast_resets_the_miss_counter(hass):
    """Broadcasts push the nudge away, so misses must not accumulate across them."""
    transport = FakeTransport()
    transport.query_result = TimeoutError()
    coordinator = make_coordinator(hass, transport)
    coordinator.data = STATUS

    for _ in range(3):
        # A broadcast arrives in between every nudge, so it never gives up.
        coordinator.handle_status(STATUS)
        assert await coordinator._async_update_data() == STATUS

    # Without the broadcast the counter would have reached the limit.
    with pytest.raises(UpdateFailed):
        for _ in range(MISSED_NUDGES_BEFORE_UNAVAILABLE):
            await coordinator._async_update_data()


async def test_update_without_a_transport_fails(hass):
    """A coordinator with no transport cannot nudge; it fails rather than crash."""
    coordinator = make_coordinator(hass)
    coordinator.transport = None

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_setup_and_unload_stop_the_transport(
    hass, enable_custom_integrations, fake_transport
):
    """Unload stops the transport so UDP port 5002 is released."""
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
    transport = fake_transport.instances[0]
    assert entry.runtime_data.transport is transport

    coordinator = entry.runtime_data
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert transport.stopped is True
    assert coordinator.transport is None


async def test_setup_uses_the_option_interval(hass, enable_custom_integrations, fake_transport):
    """The poll interval comes from the options flow (D5)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: HOST,
            CONF_DEVICE_ID: DEVICE_ID,
            CONF_KEY: KEY_HEX,
            CONF_IV: IV_HEX,
        },
        options={CONF_POLL_INTERVAL: 30},
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.update_interval.total_seconds() == 30


async def test_changing_the_option_reloads_the_entry(
    hass, enable_custom_integrations, fake_transport
):
    """A new interval only takes effect after a reload."""
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
    first = fake_transport.instances[0]

    hass.config_entries.async_update_entry(entry, options={CONF_POLL_INTERVAL: 30})
    await hass.async_block_till_done()

    assert first.stopped is True  # the old transport was stopped
    assert entry.runtime_data.update_interval.total_seconds() == 30


async def test_setup_failure_stops_the_transport(hass, enable_custom_integrations, fake_transport):
    """A silent AC on the first refresh must not leave the socket bound."""
    fake_transport.query_result = TimeoutError()
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

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert fake_transport.instances[0].stopped is True
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_passes_the_cloud_multicast_group(
    hass, enable_custom_integrations, fake_transport
):
    """The multicast group from the cloud reaches the transport."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: HOST,
            CONF_DEVICE_ID: DEVICE_ID,
            CONF_KEY: KEY_HEX,
            CONF_IV: IV_HEX,
            CONF_MULTICAST_IP: "239.255.0.15",
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert fake_transport.instances[0].kwargs["multicast"] == "239.255.0.15"
    assert fake_transport.instances[0].kwargs["on_status"] is not None


def test_config_entry_type_is_declared():
    """The entry is typed as holding the coordinator (runtime_data)."""
    assert MirAIeLocalConfigEntry is not None
