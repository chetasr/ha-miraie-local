"""Climate entity for the MirAIe AC.

State is read from the coordinator's latest status broadcast (the device only
speaks on change, so there is nothing to poll). Commands go out through
:func:`protocol.command`, and every temperature/mode/fan command also carries
``ps: "on"`` in the same frame (decision D12) so setting a value while the unit is
off turns it on.

Home Assistant 2026.11 renamed the temperature properties with a ``native_`` prefix.
This targets the pre-2026.11 names, which are correct for the confirmed HAOS
2026.10.0, and aliases them at import time so a future upgrade stays
warning-free.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MirAIeLocalConfigEntry, device_info
from .coordinator import MirAIeLocalCoordinator
from .protocol import FAN_MODES, command

_LOGGER = logging.getLogger(__name__)

# HA 2026.11 renamed the temperature properties with a `native_` prefix.
_NATIVE = hasattr(ClimateEntity, "native_current_temperature")

# The unit's own modes. Home Assistant spells fan mode "fan_only", the firmware
# "fan" (see protocol.MODES); there is no heat mode on this unit.
_HA_TO_DEVICE_MODE = {
    HVACMode.COOL: "cool",
    HVACMode.DRY: "dry",
    HVACMode.FAN_ONLY: "fan",
    HVACMode.AUTO: "auto",
}
_DEVICE_TO_HA_MODE = {value: key for key, value in _HA_TO_DEVICE_MODE.items()}

# The device reports "00.0" for a room temperature it does not know.
_UNKNOWN_TEMPERATURE = 0.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MirAIeLocalConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the climate entity for the configured AC."""
    async_add_entities([MirAIeClimate(entry.runtime_data, entry)])


class MirAIeClimate(CoordinatorEntity[MirAIeLocalCoordinator], ClimateEntity):
    """The AC as a Home Assistant climate entity."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.COOL,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
        HVACMode.AUTO,
    ]
    _attr_fan_modes = list(FAN_MODES)
    _attr_min_temp = 16.0
    _attr_max_temp = 30.0
    _attr_target_temperature_step = 0.5
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    # min_temp, max_temp and target_temperature_step were never renamed.
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: MirAIeLocalCoordinator, entry: MirAIeLocalConfigEntry) -> None:
        """Attach to the coordinator that holds the latest status."""
        super().__init__(coordinator)
        self._attr_unique_id = entry.data.get("device_id", entry.entry_id)
        self._attr_device_info = device_info(entry)

    # ---- state, read from the latest status broadcast -------------------

    @property
    def current_temperature(self) -> float | None:
        """The room temperature (``rmtmp``), or None if the unit does not know."""
        value = _as_float(self.coordinator.status.get("rmtmp"))
        if value is None or value == _UNKNOWN_TEMPERATURE:
            return None
        return value

    @property
    def target_temperature(self) -> float | None:
        """The target temperature (``actmp``)."""
        return _as_float(self.coordinator.status.get("actmp"))

    @property
    def hvac_mode(self) -> HVACMode | None:
        """``ps`` off means off; otherwise ``acmd`` names the mode."""
        status = self.coordinator.status
        if status.get("ps") == "off":
            return HVACMode.OFF
        return _DEVICE_TO_HA_MODE.get(status.get("acmd"))

    @property
    def fan_mode(self) -> str | None:
        """The fan speed (``acfs``)."""
        value = self.coordinator.status.get("acfs")
        return value if value in FAN_MODES else None

    # ---- commands ------------------------------------------------------

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature, powering the unit on (D12)."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self._async_send(command("temp", temperature))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the mode, or power off."""
        if hvac_mode == HVACMode.OFF:
            await self._async_send(command("off"))
            return
        device_mode = _HA_TO_DEVICE_MODE.get(hvac_mode)
        if device_mode is None:
            _LOGGER.warning("unsupported mode %s", hvac_mode)
            return
        await self._async_send(command("mode", device_mode))

    async def async_turn_on(self) -> None:
        """Turn on. The base class would ask for heat_cool, which this unit lacks."""
        await self.async_set_hvac_mode(HVACMode.COOL)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the fan speed, powering the unit on (D12)."""
        await self._async_send(command("fan", fan_mode))

    async def _async_send(self, payload: dict[str, Any]) -> None:
        """Send one command through the transport (the only writer)."""
        transport = self.coordinator.transport
        if transport is None:
            _LOGGER.warning("transport is not running, dropping %s", payload)
            return
        await transport.async_send(payload)


def _as_float(value: Any) -> float | None:
    """Convert a device-reported number (it sends strings) or None."""
    if value is None:
        return None
    try:
        return float(value)
    except TypeError, ValueError:
        return None


if _NATIVE:
    # HA 2026.11 renamed these with a `native_` prefix. Aliasing (and removing
    # the old names) keeps this working, and warning-free, on both.
    MirAIeClimate.native_current_temperature = MirAIeClimate.current_temperature
    MirAIeClimate.native_target_temperature = MirAIeClimate.target_temperature
    del MirAIeClimate.current_temperature
    del MirAIeClimate.target_temperature
