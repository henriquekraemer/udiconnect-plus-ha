"""Tests for Udiconnect Plus diagnostics."""

from __future__ import annotations

from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.udiconnect_plus.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_config_entry_diagnostics(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    diagnostics = await async_get_config_entry_diagnostics(hass, setup_entry)

    assert diagnostics["entry"]["data"] == {
        "email": REDACTED,
        "password": REDACTED,
        "device_uuid": REDACTED,
    }
    assert diagnostics["entry"]["title"] == REDACTED
    assert diagnostics["coordinator"]["last_update_success"] is True
    assert diagnostics["coordinator"]["update_interval"] == 30
    assert diagnostics["entry"]["data"]["device_uuid"] == REDACTED

    devices = {device["device_id"]: device for device in diagnostics["devices"]}
    assert set(devices) == {"101", "102", "103"}
    assert devices["101"]["name"] == "Persiana Sala"
    assert devices["101"]["position"] == 100
    assert devices["103"]["raw"]["category"] == "DoorLock"
    assert devices["103"]["raw"]["state"] == "locked"
    assert devices["101"]["raw"]["deviceParameters"]["ssid"] == REDACTED
    assert devices["101"]["raw"]["deviceParameters"]["physicalAddress"] == REDACTED
    assert devices["101"]["mac_address"] == REDACTED
