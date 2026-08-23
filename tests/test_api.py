"""Tests for the Udiconnect Plus API client."""

from __future__ import annotations

from unittest.mock import patch

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest

from custom_components.udiconnect_plus.api import (
    COMMAND_RETRY_DELAY,
    UdiconnectApiError,
    UdiconnectAuthError,
    UdiconnectClient,
    UdiconnectConnectionError,
    parse_devices,
)

from .conftest import (
    LOGIN_URL,
    SET_POSITION_URL,
    SYNC_URL,
    TEST_EMAIL,
    TEST_PASSWORD,
    TEST_UUID,
    CloudMock,
)


def _client(hass: HomeAssistant) -> UdiconnectClient:
    return UdiconnectClient(
        async_get_clientsession(hass), TEST_EMAIL, TEST_PASSWORD, TEST_UUID
    )


def test_parse_devices_happy_path(sync_payload) -> None:
    devices = parse_devices(sync_payload)
    assert set(devices) == {"101", "102", "103"}

    sala = devices["101"]
    assert sala.name == "Persiana Sala"
    assert sala.home_id == "71900"
    assert sala.home_name == "Casa"
    assert sala.model == "UDIN-4205"
    assert sala.controller_type == "Curtain"
    assert sala.firmware_version == "2.1.3.0"
    assert sala.latest_firmware_version == "2.1.3.0"
    assert sala.mac_address == "10:b4:1d:b6:31:70"
    assert sala.position == 100
    assert sala.is_online is True
    assert sala.low_battery is False
    assert sala.is_cover

    quarto = devices["102"]
    assert quarto.position == 0  # string "0" is accepted
    assert quarto.mac_address is None
    assert quarto.is_online is False
    assert quarto.is_calibrating is True
    assert quarto.low_battery is True
    assert quarto.new_firmware_available is True

    lock = devices["103"]
    assert lock.position is None  # "locked" is not a position
    assert not lock.is_cover


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (50, 50),
        ("75", 75),
        (33.6, 34),
        (150, 100),
        (-4, 0),
        (None, None),
        ("", None),
        ("abc", None),
        (True, None),
    ],
)
def test_parse_devices_position_values(sync_payload, state, expected) -> None:
    sync_payload["account"]["homeList"][0]["deviceList"][0]["state"] = state
    assert parse_devices(sync_payload)["101"].position == expected


def test_parse_devices_defaults_when_flags_missing(sync_payload) -> None:
    item = sync_payload["account"]["homeList"][0]["deviceList"][0]
    for key in (
        "isOnline",
        "isCalibrating",
        "lowBattery",
        "newFirmwareAvailable",
        "description",
        "category",
        "controllerType",
        "lastestFirmwareVersion",
        "deviceParameters",
    ):
        item.pop(key)
    device = parse_devices(sync_payload)["101"]
    assert device.is_online is True
    assert device.is_calibrating is False
    assert device.low_battery is False
    assert device.new_firmware_available is False
    assert device.name == "Udiconnect 101"
    assert device.latest_firmware_version is None
    assert device.mac_address is None
    assert device.is_cover  # no controllerType/category: assume it is a blind


@pytest.mark.parametrize(
    ("controller_type", "category", "expected"),
    [
        ("Curtain", "Blind", True),
        ("Curtain", None, True),
        ("DoorLock", "Blind", False),
        (None, "Blind", True),
        (None, "DoorLock", False),
        (None, "Fechadura Digital", False),
        (None, "Camera", False),
        (None, None, True),
    ],
)
def test_parse_devices_cover_detection(
    sync_payload, controller_type, category, expected
) -> None:
    item = sync_payload["account"]["homeList"][0]["deviceList"][0]
    item["controllerType"] = controller_type
    item["category"] = category
    assert parse_devices(sync_payload)["101"].is_cover is expected


def test_parse_devices_rejects_error_status(sync_payload) -> None:
    sync_payload["status"] = {"code": "500", "status": "Error"}
    with pytest.raises(UdiconnectApiError, match="500"):
        parse_devices(sync_payload)


def test_parse_devices_maintenance_mode(sync_payload) -> None:
    sync_payload["account"]["maintenanceMode"] = True
    with pytest.raises(UdiconnectConnectionError, match="maintenance"):
        parse_devices(sync_payload)


def test_parse_devices_tolerates_home_without_device_list(sync_payload) -> None:
    del sync_payload["account"]["homeList"][1]["deviceList"]
    devices = parse_devices(sync_payload)
    assert set(devices) == {"101", "102"}


def test_parse_devices_skips_items_without_id(sync_payload) -> None:
    del sync_payload["account"]["homeList"][0]["deviceList"][0]["deviceId"]
    assert set(parse_devices(sync_payload)) == {"102", "103"}


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"account": None},
        {"account": {}},
        {"account": {"homeList": None}},
        {"account": {"homeList": "nope"}},
        {"account": {"homeList": [{"homeId": "h", "deviceList": "nope"}]}},
    ],
)
def test_parse_devices_rejects_degraded_payloads(payload) -> None:
    with pytest.raises(UdiconnectApiError):
        parse_devices(payload)


def test_parse_devices_empty_home_list_means_no_devices() -> None:
    assert parse_devices({"account": {"homeList": []}}) == {}


async def test_login_and_get_devices(hass: HomeAssistant, cloud: CloudMock) -> None:
    client = _client(hass)
    assert not client.is_authenticated

    devices = await client.async_get_devices()  # logs in on demand
    assert client.is_authenticated
    assert set(devices) == {"101", "102", "103"}

    login_calls = cloud.calls_to(LOGIN_URL)
    assert len(login_calls) == 1
    body = login_calls[0][2]
    assert body["email"] == TEST_EMAIL
    assert body["password"] == TEST_PASSWORD
    assert body["mobileInformation"]["UUID"] == TEST_UUID
    assert "access-token" not in (login_calls[0][3] or {})

    sync_call = cloud.calls_to(SYNC_URL)[0]
    assert sync_call[3]["access-token"] == "token-1"
    assert "brandPlatformGUID" in sync_call[3]


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_login_rejected(
    hass: HomeAssistant, cloud: CloudMock, status: int
) -> None:
    cloud.login_status = status
    with pytest.raises(UdiconnectAuthError):
        await _client(hass).async_login()


async def test_login_without_token_is_auth_error(
    hass: HomeAssistant, cloud: CloudMock
) -> None:
    cloud.login_body = {"message": "Usuário ou senha inválidos"}
    with pytest.raises(UdiconnectAuthError, match="inválidos"):
        await _client(hass).async_login()


async def test_login_server_error(hass: HomeAssistant, cloud: CloudMock) -> None:
    cloud.login_status = 503
    with pytest.raises(UdiconnectConnectionError):
        await _client(hass).async_login()


async def test_login_unexpected_status(hass: HomeAssistant, cloud: CloudMock) -> None:
    cloud.login_status = 418
    with pytest.raises(UdiconnectApiError):
        await _client(hass).async_login()


async def test_network_errors_are_connection_errors(
    hass: HomeAssistant, cloud: CloudMock
) -> None:
    client = _client(hass)
    await client.async_login()

    cloud.sync_exc = aiohttp.ClientConnectionError("reset by peer")
    with pytest.raises(UdiconnectConnectionError):
        await client.async_get_devices()

    cloud.sync_exc = TimeoutError()
    with pytest.raises(UdiconnectConnectionError, match="Timeout"):
        await client.async_get_devices()


async def test_server_error_is_connection_error(
    hass: HomeAssistant, cloud: CloudMock
) -> None:
    client = _client(hass)
    await client.async_login()
    cloud.sync_status = 503
    with pytest.raises(UdiconnectConnectionError):
        await client.async_get_devices()


async def test_expired_token_triggers_relogin(
    hass: HomeAssistant, cloud: CloudMock
) -> None:
    client = _client(hass)
    await client.async_login()
    assert len(cloud.calls_to(LOGIN_URL)) == 1

    cloud.rejected_tokens.add("token-1")
    cloud.login_body = {"accessToken": "token-2"}
    devices = await client.async_get_devices()

    assert set(devices) == {"101", "102", "103"}
    assert len(cloud.calls_to(LOGIN_URL)) == 2
    assert cloud.calls_to(SYNC_URL)[-1][3]["access-token"] == "token-2"


async def test_relogin_still_rejected_is_auth_error(
    hass: HomeAssistant, cloud: CloudMock
) -> None:
    client = _client(hass)
    await client.async_login()
    cloud.rejected_tokens.add("token-1")  # login keeps handing out token-1
    with pytest.raises(UdiconnectAuthError):
        await client.async_get_devices()
    assert len(cloud.calls_to(LOGIN_URL)) == 2


async def test_set_position(hass: HomeAssistant, cloud: CloudMock) -> None:
    client = _client(hass)
    await client.async_set_position("101", 42)
    call = cloud.calls_to(SET_POSITION_URL)[0]
    assert call[2] == {
        "action": "CurtainSetPosition",
        "deviceId": 101,
        "position": 42,
    }


async def test_stop(hass: HomeAssistant, cloud: CloudMock) -> None:
    await _client(hass).async_set_position("101", 0)
    await _client(hass).async_stop("101")
    call = cloud.calls_to(SET_POSITION_URL)[-1]
    assert call[2] == {"action": "CurtainStop", "deviceId": 101}


async def test_stop_refused_by_cloud(hass: HomeAssistant, cloud: CloudMock) -> None:
    cloud.set_body = {"result": False}
    with (
        patch("custom_components.udiconnect_plus.api.asyncio.sleep"),
        pytest.raises(UdiconnectApiError, match="CurtainStop"),
    ):
        await _client(hass).async_stop("101")
    assert len(cloud.calls_to(SET_POSITION_URL)) == 2


async def test_set_position_keeps_non_numeric_ids(
    hass: HomeAssistant, cloud: CloudMock
) -> None:
    await _client(hass).async_set_position("abc-1", 10)
    assert cloud.calls_to(SET_POSITION_URL)[0][2]["deviceId"] == "abc-1"


@pytest.mark.parametrize("position", [-1, 101])
async def test_set_position_validates_range(
    hass: HomeAssistant, cloud: CloudMock, position: int
) -> None:
    with pytest.raises(ValueError):
        await _client(hass).async_set_position("101", position)
    assert not cloud.calls_to(SET_POSITION_URL)


async def test_set_position_refused_by_cloud(
    hass: HomeAssistant, cloud: CloudMock
) -> None:
    cloud.set_body = {"result": False, "message": "Device offline"}
    with (
        patch("custom_components.udiconnect_plus.api.asyncio.sleep") as mock_sleep,
        pytest.raises(UdiconnectApiError, match="Device offline"),
    ):
        await _client(hass).async_set_position("101", 10)
    assert len(cloud.calls_to(SET_POSITION_URL)) == 2
    mock_sleep.assert_awaited_once_with(COMMAND_RETRY_DELAY)


async def test_set_position_retries_once(hass: HomeAssistant, cloud: CloudMock) -> None:
    bodies = iter([{"responseStatus": None, "result": False}, {"result": True}])
    cloud.set_body_fn = lambda: next(bodies)
    with patch("custom_components.udiconnect_plus.api.asyncio.sleep"):
        await _client(hass).async_set_position("101", 10)
    calls = cloud.calls_to(SET_POSITION_URL)
    assert len(calls) == 2
    assert calls[0][2] == calls[1][2]


async def test_invalid_json_is_api_error(
    hass: HomeAssistant, aioclient_mock, cloud: CloudMock
) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.post(LOGIN_URL, text="<html>maintenance</html>")
    with pytest.raises(UdiconnectApiError, match="Invalid JSON"):
        await _client(hass).async_login()
