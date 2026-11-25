"""DataUpdateCoordinator for Udiconnect Plus."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    UdiconnectApiError,
    UdiconnectAuthError,
    UdiconnectClient,
    UdiconnectConnectionError,
    UdiDevice,
)
from .const import CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE, DOMAIN, MODE_CLOUD_ONLY
from .local import UdiLocalManager

_LOGGER = logging.getLogger(__name__)

REQUEST_REFRESH_COOLDOWN = 2.0

type UdiconnectConfigEntry = ConfigEntry[UdiconnectCoordinator]


class UdiconnectCoordinator(DataUpdateCoordinator[dict[str, UdiDevice]]):
    """Fetch device state from the cloud and hold the LAN listener."""

    config_entry: UdiconnectConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: UdiconnectConfigEntry,
        client: UdiconnectClient,
        scan_interval: int,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=REQUEST_REFRESH_COOLDOWN, immediate=False
            ),
        )
        self.client = client
        self.control_mode: str = entry.options.get(
            CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE
        )
        # In cloud-only mode nothing local is opened.
        self.local_enabled = self.control_mode != MODE_CLOUD_ONLY
        # The LAN state lives in the local manager, not in self.data: a cloud
        # poll would overwrite it, and async_set_updated_data would mark a
        # failing cloud as healthy. Entities read both and pick.
        self.local_manager = UdiLocalManager(self._async_local_changed)

    @callback
    def _async_local_changed(self) -> None:
        """Refresh the entities when something changes on the LAN."""
        if self.data is not None:
            self.async_update_listeners()

    @callback
    def async_check_local(self, _now: datetime) -> None:
        """Look for modules that went quiet: nothing else reports it."""
        self.local_manager.expire()

    async def _async_update_data(self) -> dict[str, UdiDevice]:
        try:
            devices = await self.client.async_get_devices()
        except UdiconnectAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from err
        except UdiconnectConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        except UdiconnectApiError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="unexpected_response",
                translation_placeholders={"error": str(err)},
            ) from err

        if self.data is not None:
            for unique_id in devices.keys() - self.data.keys():
                device = devices[unique_id]
                _LOGGER.info("New device found: %s (%s)", device.name, device.device_id)

        self.local_manager.set_macs(device.mac_address for device in devices.values())
        return devices
