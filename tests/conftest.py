"""Fixtures for Udiconnect Plus tests."""

from __future__ import annotations

from collections.abc import Callable, Generator
import copy
import json
import pathlib
from typing import Any

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.udiconnect_plus.const import (
    API_BASE_URL,
    CONF_DEVICE_UUID,
    DOMAIN,
)

LOGIN_URL = f"{API_BASE_URL}/Account/Login"
SYNC_URL = f"{API_BASE_URL}/App/SyncAccount"
SET_POSITION_URL = f"{API_BASE_URL}/Curtain/SetPositionCurtain"

TEST_EMAIL = "user@example.com"
TEST_PASSWORD = "hunter2"
TEST_UUID = "11111111-2222-3333-4444-555555555555"

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations."""


@pytest.fixture
def sync_payload() -> dict[str, Any]:
    """Return the SyncAccount fixture payload."""
    return json.loads((_FIXTURES / "sync_account.json").read_text())


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=TEST_EMAIL,
        unique_id=TEST_EMAIL,
        version=2,
        minor_version=1,
        data={
            CONF_EMAIL: TEST_EMAIL,
            CONF_PASSWORD: TEST_PASSWORD,
            CONF_DEVICE_UUID: TEST_UUID,
        },
    )


class CloudMock:
    """Fake Udiconnect cloud on top of aioclient_mock."""

    def __init__(
        self, aioclient_mock: AiohttpClientMocker, payload: dict[str, Any]
    ) -> None:
        self._mock = aioclient_mock
        self.payload = payload
        self.login_status = 200
        self.login_body: dict[str, Any] = {"accessToken": "token-1"}
        self.sync_status = 200
        self.sync_exc: Exception | None = None
        self.set_status = 200
        self.set_body: dict[str, Any] = {"result": True}
        self.set_body_fn: Callable[[], dict[str, Any]] | None = None
        self.set_exc: Exception | None = None
        self.rejected_tokens: set[str] = set()
        self._register()

    def _register(self) -> None:
        self._mock.post(LOGIN_URL, side_effect=self._login)
        self._mock.post(SYNC_URL, side_effect=self._sync)
        self._mock.post(SET_POSITION_URL, side_effect=self._set_position)

    @staticmethod
    def _response(status: int, body: Any) -> AiohttpClientMockResponse:
        return AiohttpClientMockResponse("post", URL(""), status=status, json=body)

    async def _login(self, method, url, data):
        return self._response(self.login_status, self.login_body)

    def _token_rejected(self) -> bool:
        # aioclient_mock records (method, url, data, headers) before the side effect runs
        headers = self._mock.mock_calls[-1][3] or {}
        return headers.get("access-token") in self.rejected_tokens

    async def _sync(self, method, url, data):
        if self.sync_exc is not None:
            raise self.sync_exc
        if self._token_rejected():
            return self._response(401, {"message": "Unauthorized"})
        return self._response(self.sync_status, copy.deepcopy(self.payload))

    async def _set_position(self, method, url, data):
        if self.set_exc is not None:
            raise self.set_exc
        if self._token_rejected():
            return self._response(401, {"message": "Unauthorized"})
        body = self.set_body_fn() if self.set_body_fn else self.set_body
        return self._response(self.set_status, body)

    @property
    def calls(self):
        return self._mock.mock_calls

    def calls_to(self, url: str) -> list[Any]:
        target = URL(url)
        return [call for call in self._mock.mock_calls if call[1] == target]

    def device(self, device_id: str) -> dict[str, Any]:
        for home in self.payload["account"]["homeList"]:
            for device in home["deviceList"]:
                if str(device["deviceId"]) == device_id:
                    return device
        raise KeyError(device_id)


@pytest.fixture
def cloud(
    aioclient_mock: AiohttpClientMocker, sync_payload: dict[str, Any]
) -> CloudMock:
    """Return the fake cloud."""
    return CloudMock(aioclient_mock, sync_payload)


async def setup_integration(hass: HomeAssistant, entry: MockConfigEntry) -> bool:
    """Set up the integration."""
    entry.add_to_hass(hass)
    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return result


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, cloud: CloudMock
) -> Generator[MockConfigEntry]:
    """Set up the integration with a mock config entry."""
    assert await setup_integration(hass, mock_config_entry)
    yield mock_config_entry
