"""Binary sensor platform for Udiconnect Plus."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import UdiDevice
from .coordinator import UdiconnectConfigEntry, UdiconnectCoordinator
from .entity import UdiconnectEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class UdiconnectBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Udiconnect Plus binary sensor."""

    value_fn: Callable[[UdiDevice], bool]


SENSORS: tuple[UdiconnectBinarySensorDescription, ...] = (
    UdiconnectBinarySensorDescription(
        key="connectivity",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.is_online,
    ),
    UdiconnectBinarySensorDescription(
        key="low_battery",
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.low_battery,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UdiconnectConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Udiconnect Plus binary sensors."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _async_add_new_sensors() -> None:
        new_ids = [
            device_id for device_id in coordinator.data if device_id not in known
        ]
        if not new_ids:
            return
        known.update(new_ids)
        async_add_entities(
            UdiconnectBinarySensor(coordinator, device_id, description)
            for device_id in new_ids
            for description in SENSORS
        )

    _async_add_new_sensors()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_sensors))


class UdiconnectBinarySensor(UdiconnectEntity, BinarySensorEntity):
    """Udiconnect Plus binary sensor."""

    entity_description: UdiconnectBinarySensorDescription

    def __init__(
        self,
        coordinator: UdiconnectCoordinator,
        device_id: str,
        description: UdiconnectBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_translation_key = description.key
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return the state of the sensor."""
        device = self.device
        return None if device is None else self.entity_description.value_fn(device)
