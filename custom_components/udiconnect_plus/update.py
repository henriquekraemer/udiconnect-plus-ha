"""Update platform for Udiconnect Plus."""

from __future__ import annotations

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import UdiconnectConfigEntry, UdiconnectCoordinator
from .entity import UdiconnectEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UdiconnectConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Udiconnect Plus update entities."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _async_add_new_entities() -> None:
        new_ids = [
            device_id for device_id in coordinator.data if device_id not in known
        ]
        if not new_ids:
            return
        known.update(new_ids)
        async_add_entities(
            UdiconnectFirmwareUpdate(coordinator, device_id) for device_id in new_ids
        )

    _async_add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))


class UdiconnectFirmwareUpdate(UdiconnectEntity, UpdateEntity):
    """Firmware update entity. Installing is only possible from the app."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "firmware"

    def __init__(self, coordinator: UdiconnectCoordinator, device_id: str) -> None:
        """Initialize the update entity."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_firmware"

    @property
    def installed_version(self) -> str | None:
        """Return the installed firmware version."""
        device = self.device
        return device.firmware_version if device else None

    @property
    def latest_version(self) -> str | None:
        """Return the latest firmware version."""
        device = self.device
        if device is None:
            return None
        if device.latest_firmware_version:
            return device.latest_firmware_version
        return None if device.new_firmware_available else device.firmware_version
