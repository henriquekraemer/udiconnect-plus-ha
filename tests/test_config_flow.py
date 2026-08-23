"""Tests for the Udiconnect Plus config flow."""

from __future__ import annotations

import aiohttp
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.udiconnect_plus.const import (
    CONF_DEVICE_UUID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

from .conftest import LOGIN_URL, TEST_EMAIL, TEST_PASSWORD, CloudMock


async def test_user_flow_creates_entry(hass: HomeAssistant, cloud: CloudMock) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "  User@Example.com ", CONF_PASSWORD: TEST_PASSWORD},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "User@Example.com"
    entry = result["result"]
    assert entry.unique_id == "user@example.com"
    assert entry.data[CONF_EMAIL] == "User@Example.com"
    assert entry.data[CONF_PASSWORD] == TEST_PASSWORD
    assert len(entry.data[CONF_DEVICE_UUID]) == 36
    assert entry.version == 2

    assert (
        cloud.calls_to(LOGIN_URL)[0][2]["mobileInformation"]["UUID"]
        == entry.data[CONF_DEVICE_UUID]
    )


@pytest.mark.parametrize(
    ("login_status", "login_body", "login_exc", "error"),
    [
        (401, {}, None, "invalid_auth"),
        (200, {"message": "nope"}, None, "invalid_auth"),
        (503, {}, None, "cannot_connect"),
        (200, {}, aiohttp.ClientConnectionError("boom"), "cannot_connect"),
        (200, {}, TimeoutError(), "cannot_connect"),
        (418, {}, None, "unknown"),
    ],
)
async def test_user_flow_errors_then_recovers(
    hass: HomeAssistant,
    cloud: CloudMock,
    aioclient_mock,
    login_status,
    login_body,
    login_exc,
    error,
) -> None:
    if login_exc is not None:
        aioclient_mock.clear_requests()
        aioclient_mock.post(LOGIN_URL, exc=login_exc)
    else:
        cloud.login_status = login_status
        cloud.login_body = login_body

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: TEST_EMAIL, CONF_PASSWORD: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    aioclient_mock.clear_requests()
    CloudMock(aioclient_mock, cloud.payload)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: TEST_EMAIL, CONF_PASSWORD: TEST_PASSWORD}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_aborts_when_account_exists(
    hass: HomeAssistant, cloud: CloudMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: TEST_EMAIL.upper(), CONF_PASSWORD: TEST_PASSWORD},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert not cloud.calls_to(LOGIN_URL)


async def test_reauth_flow(
    hass: HomeAssistant, cloud: CloudMock, setup_entry: MockConfigEntry
) -> None:
    result = await setup_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"][CONF_EMAIL] == TEST_EMAIL

    cloud.login_status = 401
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "still-wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    cloud.login_status = 200
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-password"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert setup_entry.data[CONF_PASSWORD] == "new-password"
    assert setup_entry.data[CONF_EMAIL] == TEST_EMAIL
    assert setup_entry.state is config_entries.ConfigEntryState.LOADED


async def test_options_flow_updates_interval_without_reload(
    hass: HomeAssistant, cloud: CloudMock, setup_entry: MockConfigEntry
) -> None:
    coordinator = setup_entry.runtime_data
    assert coordinator.update_interval.total_seconds() == DEFAULT_SCAN_INTERVAL

    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 120}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_entry.options == {CONF_SCAN_INTERVAL: 120}
    assert setup_entry.runtime_data is coordinator
    assert coordinator.update_interval.total_seconds() == 120
