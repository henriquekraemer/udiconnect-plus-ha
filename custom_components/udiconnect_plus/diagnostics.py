"""Diagnostics support for Udiconnect Plus."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import CONF_DEVICE_UUID
from .coordinator import UdiconnectConfigEntry

TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_DEVICE_UUID,
    "accessToken",
    "access-token",
    "email",
    "firstName",
    "lastName",
    "phone",
    "phoneNumber",
    "cpf",
    "address",
    "latitude",
    "longitude",
    "unique_id",
    "title",
    "ssid",
    "physicalAddress",
    "mac_address",
    "deviceUS",
    "deviceSS",
    "homeUS",
    "homeSS",
    "accessTokens",
    "thirdPartyData",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: UdiconnectConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
        },
        "devices": async_redact_data(
            [asdict(device) for device in coordinator.data.values()],
            TO_REDACT,
        ),
    }
