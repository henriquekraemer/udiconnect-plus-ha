"""Base entity for Udiconnect Plus."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import UdiDevice
from .const import DOMAIN, MANUFACTURER
from .coordinator import UdiconnectCoordinator


class UdiconnectEntity(CoordinatorEntity[UdiconnectCoordinator]):
    """Base class for Udiconnect Plus entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: UdiconnectCoordinator, device_key: str) -> None:
        """Initialize the entity. device_key is the UdiDevice.unique_id."""
        super().__init__(coordinator)
        self._device_key = device_key
        device = coordinator.data[device_key]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_key)},
            connections=(
                {(CONNECTION_NETWORK_MAC, device.mac_address)}
                if device.mac_address
                else set()
            ),
            name=device.name,
            manufacturer=MANUFACTURER,
            model=device.model,
            sw_version=device.firmware_version,
            suggested_area=device.home_name,
        )

    @property
    def device(self) -> UdiDevice | None:
        """Return the device data from the last update."""
        return self.coordinator.data.get(self._device_key)

    @property
    def available(self) -> bool:
        """Return True if the device is still reported by the cloud."""
        return super().available and self.device is not None
