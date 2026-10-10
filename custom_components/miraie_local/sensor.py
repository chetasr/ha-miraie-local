"""Diagnostic sensors for the MirAIe AC.

All of them read the coordinator's latest status broadcast, so they are pushed
rather than polled and never talk to the device themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MirAIeLocalConfigEntry, device_info
from .coordinator import MirAIeLocalCoordinator

# The device reports "00.0" for a temperature it does not know.
_UNKNOWN_TEMPERATURE = 0.0


@dataclass(frozen=True, kw_only=True)
class MirAIeSensorDescription(SensorEntityDescription):
    """A diagnostic sensor backed by one status-broadcast field."""

    zero_means_unknown: bool = False
    whole_numbers: bool = False


SENSORS: tuple[MirAIeSensorDescription, ...] = (
    MirAIeSensorDescription(
        key="rssi",
        translation_key="rssi",
        name="RSSI",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        whole_numbers=True,
    ),
    MirAIeSensorDescription(
        key="rmtmp",
        translation_key="room_temperature",
        name="Room temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        zero_means_unknown=True,
    ),
    MirAIeSensorDescription(
        key="V",
        translation_key="firmware_version",
        name="Firmware version",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MirAIeLocalConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the diagnostic sensors for the configured AC."""
    coordinator = entry.runtime_data
    async_add_entities(MirAIeSensor(coordinator, entry, description) for description in SENSORS)


class MirAIeSensor(CoordinatorEntity[MirAIeLocalCoordinator], SensorEntity):
    """One diagnostic sensor, driven by the status broadcast."""

    _attr_has_entity_name = True
    entity_description: MirAIeSensorDescription

    def __init__(
        self,
        coordinator: MirAIeLocalCoordinator,
        entry: MirAIeLocalConfigEntry,
        description: MirAIeSensorDescription,
    ) -> None:
        """Attach to the coordinator and give the sensor a stable id."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = device_info(entry)

    @property
    def native_value(self) -> Any:
        """The field from the latest status, as a number where possible."""
        value = self.coordinator.status.get(self.entity_description.key)
        if value is None:
            return None
        try:
            number = float(value)
        except TypeError, ValueError:
            return str(value)
        if self.entity_description.zero_means_unknown and number == _UNKNOWN_TEMPERATURE:
            return None
        # RSSI is a whole number; HA renders a float as "-51.0".
        if self.entity_description.whole_numbers and number.is_integer():
            return int(number)
        return number
