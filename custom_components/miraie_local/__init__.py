"""The MirAIe Local integration - local UDP control of a Panasonic MirAIe AC."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_IV,
    CONF_KEY,
    CONF_MULTICAST_IP,
    CONF_POLL_INTERVAL,
    DEFAULT_MULTICAST,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .coordinator import MirAIeLocalCoordinator
from .transport import MirAIeLocalTransport

__all__ = ["DOMAIN", "MirAIeLocalConfigEntry", "device_info"]

PLATFORMS: list[Platform] = [Platform.CLIMATE, Platform.SENSOR]

MirAIeLocalConfigEntry = ConfigEntry[MirAIeLocalCoordinator]


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """The device registry entry shared by every entity of this AC."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Panasonic AC",
        manufacturer=MANUFACTURER,
        model=MODEL,
    )


async def async_setup_entry(hass: HomeAssistant, entry: MirAIeLocalConfigEntry) -> bool:
    """Build the transport and coordinator, then forward to the platforms."""
    interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
    coordinator = MirAIeLocalCoordinator(hass, update_interval_seconds=interval)
    transport = MirAIeLocalTransport(
        hass.loop,
        entry.data[CONF_HOST],
        entry.data[CONF_DEVICE_ID],
        bytes.fromhex(entry.data[CONF_KEY]),
        bytes.fromhex(entry.data[CONF_IV]),
        on_status=coordinator.handle_status,
        multicast=entry.data.get(CONF_MULTICAST_IP, DEFAULT_MULTICAST),
    )
    coordinator.transport = transport
    await transport.async_start()

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    try:
        # The first refresh is the cold-start nudge: if the AC never answers,
        # setup fails and Home Assistant retries it with backoff.
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await transport.async_stop()
        raise

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MirAIeLocalConfigEntry) -> bool:
    """Unload the entry, stopping the transport so the socket is released."""
    coordinator = entry.runtime_data
    if coordinator.transport is not None:
        await coordinator.transport.async_stop()
        coordinator.transport = None
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(hass: HomeAssistant, entry: MirAIeLocalConfigEntry) -> None:
    """Reload so a changed poll interval takes effect."""
    await hass.config_entries.async_reload(entry.entry_id)
