"""DataUpdateCoordinator for Udiconnect Plus."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

REQUEST_REFRESH_COOLDOWN = 2.0

type UdiconnectConfigEntry = ConfigEntry[UdiconnectCoordinator]


class UdiconnectCoordinator(DataUpdateCoordinator[dict[str, UdiDevice]]):
    """Fetch device state from the cloud."""

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
            name=f"{DOMAIN} ({entry.title})",
            update_interval=timedelta(seconds=scan_interval),
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=REQUEST_REFRESH_COOLDOWN, immediate=False
            ),
        )
        self.client = client

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
            for device_id in devices.keys() - self.data.keys():
                _LOGGER.info(
                    "New device found: %s (%s)", devices[device_id].name, device_id
                )
        return devices
