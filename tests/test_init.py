"""Tests for Udiconnect Plus setup and migration."""

from __future__ import annotations

import aiohttp
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.udiconnect_plus.const import CONF_DEVICE_UUID, DOMAIN

from .conftest import LOGIN_URL, TEST_EMAIL, TEST_PASSWORD, CloudMock, setup_integration


async def test_setup_and_unload(
    hass: HomeAssistant, cloud: CloudMock, mock_config_entry: MockConfigEntry
) -> None:
    assert await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    assert hass.states.get("cover.persiana_sala") is not None
    assert hass.states.get("cover.persiana_quarto") is not None
    assert hass.states.get("cover.fechadura_entrada") is None
    assert hass.states.get("binary_sensor.persiana_sala_connectivity") is not None
    assert hass.states.get("binary_sensor.persiana_sala_low_battery") is not None
    assert hass.states.get("binary_sensor.fechadura_entrada_connectivity") is not None
    assert hass.states.get("update.persiana_sala_firmware") is not None

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, "101")})
    assert device is not None
    assert device.name == "Persiana Sala"
    assert device.manufacturer == "Udinese"
    assert device.model == "UDIN-4205"
    assert device.sw_version == "2.1.3.0"
    assert device.connections == {(dr.CONNECTION_NETWORK_MAC, "10:b4:1d:b6:31:70")}

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_invalid_auth_starts_reauth(
    hass: HomeAssistant, cloud: CloudMock, mock_config_entry: MockConfigEntry
) -> None:
    cloud.login_status = 401
    assert not await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH


async def test_setup_cloud_unreachable_retries(
    hass: HomeAssistant,
    cloud: CloudMock,
    mock_config_entry: MockConfigEntry,
    aioclient_mock,
) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.post(LOGIN_URL, exc=aiohttp.ClientConnectionError("down"))
    assert not await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_degraded_payload_retries(
    hass: HomeAssistant, cloud: CloudMock, mock_config_entry: MockConfigEntry
) -> None:
    cloud.payload = {"account": {}}
    assert not await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_migrate_legacy_v1_entry(hass: HomeAssistant, cloud: CloudMock) -> None:
    legacy = MockConfigEntry(
        domain=DOMAIN,
        title=f"Udiconnect Plus ({TEST_EMAIL})",
        version=1,
        data={"E-Mail": TEST_EMAIL, "Password": TEST_PASSWORD},
    )
    legacy.add_to_hass(hass)

    registry = er.async_get(hass)
    registry.async_get_or_create(
        "cover",
        DOMAIN,
        "udiconnect_plus_device_id_101",
        config_entry=legacy,
        suggested_object_id="persiana_sala_antiga",
    )

    assert await hass.config_entries.async_setup(legacy.entry_id)
    await hass.async_block_till_done()

    assert legacy.state is ConfigEntryState.LOADED
    assert legacy.version == 2
    assert legacy.unique_id == TEST_EMAIL
    assert legacy.data[CONF_EMAIL] == TEST_EMAIL
    assert legacy.data[CONF_PASSWORD] == TEST_PASSWORD
    assert len(legacy.data[CONF_DEVICE_UUID]) == 36
    assert "E-Mail" not in legacy.data

    entry = registry.async_get("cover.persiana_sala_antiga")
    assert entry is not None
    assert entry.unique_id == "101"
    assert hass.states.get("cover.persiana_sala_antiga").state == "open"


async def test_migrate_legacy_entry_without_credentials_fails(
    hass: HomeAssistant, cloud: CloudMock
) -> None:
    legacy = MockConfigEntry(domain=DOMAIN, version=1, data={"E-Mail": TEST_EMAIL})
    legacy.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(legacy.entry_id)
    assert legacy.state is ConfigEntryState.MIGRATION_ERROR


async def test_future_version_is_not_downgraded(
    hass: HomeAssistant, cloud: CloudMock
) -> None:
    future = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        data={
            CONF_EMAIL: TEST_EMAIL,
            CONF_PASSWORD: TEST_PASSWORD,
            CONF_DEVICE_UUID: "x",
        },
    )
    future.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(future.entry_id)
    assert future.state is ConfigEntryState.MIGRATION_ERROR


async def test_remove_stale_device(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    cloud: CloudMock,
    setup_entry: MockConfigEntry,
) -> None:
    assert await async_setup_component(hass, "config", {})
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, "101")})
    assert device is not None

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "config/device_registry/remove_config_entry",
            "config_entry_id": setup_entry.entry_id,
            "device_id": device.id,
        }
    )
    response = await client.receive_json()
    assert not response["success"]  # still reported by the cloud

    cloud.payload["account"]["homeList"][0]["deviceList"].pop(0)
    await setup_entry.runtime_data.async_refresh()
    await client.send_json_auto_id(
        {
            "type": "config/device_registry/remove_config_entry",
            "config_entry_id": setup_entry.entry_id,
            "device_id": device.id,
        }
    )
    response = await client.receive_json()
    assert response["success"]
    assert device_registry.async_get_device(identifiers={(DOMAIN, "101")}) is None
