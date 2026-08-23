"""Client for the Udiconnect Plus cloud API (Yale Connect platform)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from typing import Any

import aiohttp

from .const import (
    API_APP_VERSION,
    API_BASE_URL,
    API_BRAND_PLATFORM_GUID,
    API_TIMEOUT_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

_LOGIN_PATH = "/Account/Login"
_SYNC_ACCOUNT_PATH = "/App/SyncAccount"
# Single endpoint for all curtain commands; the "action" field selects the command.
_CURTAIN_PATH = "/Curtain/SetPositionCurtain"

# The cloud forwards commands to the motor and answers result=false when it
# gets no ack in time, which happens now and then. One retry covers it.
COMMAND_RETRIES = 2
COMMAND_RETRY_DELAY = 1.5

_COVER_CONTROLLER_TYPES = ("curtain", "blind", "shutter")
_NON_COVER_CATEGORY_HINTS = ("lock", "fechadura", "camera", "câmera", "gateway", "hub")


class UdiconnectError(Exception):
    """Base exception for the client."""


class UdiconnectAuthError(UdiconnectError):
    """Credentials rejected."""


class UdiconnectConnectionError(UdiconnectError):
    """Cloud unreachable or returned a server error."""


class UdiconnectApiError(UdiconnectError):
    """Unexpected response from the cloud."""


@dataclass(frozen=True, slots=True)
class UdiDevice:
    """Device as reported by SyncAccount."""

    device_id: str
    name: str
    home_id: str | None
    home_name: str | None
    category: str | None
    controller_type: str | None
    model: str | None
    firmware_version: str | None
    latest_firmware_version: str | None
    mac_address: str | None
    position: int | None
    is_online: bool
    is_calibrating: bool
    low_battery: bool
    new_firmware_available: bool
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def is_cover(self) -> bool:
        """Return True if the device should get a cover entity."""
        if self.controller_type is not None:
            return self.controller_type.lower() in _COVER_CONTROLLER_TYPES
        if self.category is None:
            return True
        category = self.category.lower()
        return not any(hint in category for hint in _NON_COVER_CATEGORY_HINTS)


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
    return default


def _as_position(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        position = round(float(value))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, position))


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_mac(value: Any) -> str | None:
    text = _as_str(value)
    if text is None:
        return None
    mac = text.lower().replace("-", ":")
    parts = mac.split(":")
    if len(parts) != 6 or not all(len(part) == 2 for part in parts):
        return None
    return mac


def _cloud_device_id(device_id: str) -> int | str:
    # The cloud uses integer ids; keep strings for anything else.
    return int(device_id) if device_id.isdigit() else device_id


def _parse_device(
    item: dict[str, Any], home_id: str | None, home_name: str | None
) -> UdiDevice | None:
    device_id = _as_str(item.get("deviceId"))
    if device_id is None:
        _LOGGER.debug("Skipping device without deviceId: %s", item)
        return None
    parameters = item.get("deviceParameters")
    if not isinstance(parameters, dict):
        parameters = {}
    return UdiDevice(
        device_id=device_id,
        name=_as_str(item.get("description")) or f"Udiconnect {device_id}",
        home_id=home_id,
        home_name=home_name,
        category=_as_str(item.get("category")),
        controller_type=_as_str(item.get("controllerType")),
        model=_as_str(item.get("deviceModelDescription")),
        firmware_version=_as_str(item.get("currentFirmwareVersion")),
        # "lastest" is how the API spells it
        latest_firmware_version=_as_str(item.get("lastestFirmwareVersion"))
        or _as_str(item.get("latestFirmwareVersion")),
        mac_address=_as_mac(parameters.get("physicalAddress")),
        position=_as_position(item.get("state")),
        is_online=_as_bool(item.get("isOnline"), default=True),
        is_calibrating=_as_bool(item.get("isCalibrating"), default=False),
        low_battery=_as_bool(item.get("lowBattery"), default=False),
        new_firmware_available=_as_bool(
            item.get("newFirmwareAvailable"), default=False
        ),
        raw=item,
    )


def _home_list(payload: Any) -> list[Any]:
    if not isinstance(payload, dict):
        raise UdiconnectApiError("SyncAccount response is not a JSON object")

    status = payload.get("status")
    if isinstance(status, dict) and str(status.get("code", "200")) != "200":
        raise UdiconnectApiError(
            f"SyncAccount returned status {status.get('code')}: {status.get('status')}"
        )

    account = payload.get("account")
    if not isinstance(account, dict):
        raise UdiconnectApiError("SyncAccount response has no 'account'")
    if _as_bool(account.get("maintenanceMode"), default=False):
        raise UdiconnectConnectionError("Cloud is in maintenance mode")

    home_list = account.get("homeList")
    if home_list is None:
        raise UdiconnectApiError("SyncAccount response has no 'homeList'")
    if not isinstance(home_list, list):
        raise UdiconnectApiError("SyncAccount 'homeList' is not a list")
    return home_list


def parse_devices(payload: Any) -> dict[str, UdiDevice]:
    """Parse a SyncAccount payload into devices keyed by device id.

    Raises UdiconnectApiError on a malformed payload. The cloud occasionally
    answers 200 with an incomplete body; that must not look like "no devices".
    """
    devices: dict[str, UdiDevice] = {}
    for home in _home_list(payload):
        if not isinstance(home, dict):
            continue
        home_id = _as_str(home.get("homeId"))
        home_name = _as_str(home.get("description"))
        device_list = home.get("deviceList")
        if device_list is None:
            _LOGGER.debug("Home %s has no deviceList", home_id)
            continue
        if not isinstance(device_list, list):
            raise UdiconnectApiError(f"deviceList of home {home_id} is not a list")

        for item in device_list:
            if not isinstance(item, dict):
                continue
            device = _parse_device(item, home_id, home_name)
            if device is not None:
                devices[device.device_id] = device
    return devices


@dataclass(frozen=True, slots=True)
class _Response:
    status: int
    data: Any
    valid_json: bool


class UdiconnectClient:
    """Udiconnect Plus API client."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        device_uuid: str,
        *,
        base_url: str = API_BASE_URL,
        timeout: float = API_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._email = email
        self._password = password
        self._device_uuid = device_uuid
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._access_token: str | None = None
        self._login_lock = asyncio.Lock()

    @property
    def is_authenticated(self) -> bool:
        """Return True if an access token is held."""
        return self._access_token is not None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "brandPlatformGUID": API_BRAND_PLATFORM_GUID,
        }
        if self._access_token:
            headers["access-token"] = self._access_token
        return headers

    async def _post(self, path: str, payload: dict[str, Any]) -> _Response:
        try:
            async with self._session.post(
                f"{self._base_url}{path}",
                json=payload,
                headers=self._headers(),
                timeout=self._timeout,
            ) as response:
                status = response.status
                try:
                    data = await response.json(content_type=None)
                except (aiohttp.ClientError, ValueError):
                    return _Response(status, None, valid_json=False)
        except TimeoutError as err:
            raise UdiconnectConnectionError(f"Timeout calling {path}") from err
        except aiohttp.ClientError as err:
            raise UdiconnectConnectionError(f"Error calling {path}: {err}") from err
        return _Response(status, data, valid_json=True)

    async def async_login(self) -> None:
        """Log in and store the access token."""
        async with self._login_lock:
            await self._async_login()

    async def _async_login(self) -> None:
        self._access_token = None
        payload = {
            "email": self._email,
            "password": self._password,
            "mobileInformation": {
                "os": "Android 14",
                "information": "Home Assistant",
                "appVersion": API_APP_VERSION,
                "pushToken": "home-assistant",
                "UUID": self._device_uuid,
            },
        }
        response = await self._post(_LOGIN_PATH, payload)
        if response.status in (400, 401, 403):
            raise UdiconnectAuthError(f"Login rejected with HTTP {response.status}")
        if response.status >= 500:
            raise UdiconnectConnectionError(f"Login failed with HTTP {response.status}")
        if response.status != 200:
            raise UdiconnectApiError(f"Unexpected HTTP {response.status} on login")
        if not response.valid_json:
            raise UdiconnectApiError(f"Invalid JSON from {_LOGIN_PATH}")

        data = response.data
        token = data.get("accessToken") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            message = data.get("message") if isinstance(data, dict) else None
            raise UdiconnectAuthError(
                f"Login returned no access token ({message or 'no message'})"
            )
        self._access_token = token
        _LOGGER.debug("Logged in")

    async def _async_request(self, path: str, payload: dict[str, Any]) -> Any:
        if self._access_token is None:
            await self.async_login()

        response = await self._post(path, payload)
        if response.status in (401, 403):
            _LOGGER.debug("Token rejected on %s, logging in again", path)
            await self.async_login()
            response = await self._post(path, payload)
            if response.status in (401, 403):
                raise UdiconnectAuthError(
                    f"{path} rejected after re-login (HTTP {response.status})"
                )

        if response.status >= 500:
            raise UdiconnectConnectionError(
                f"Server error on {path}: HTTP {response.status}"
            )
        if response.status != 200:
            raise UdiconnectApiError(f"Unexpected HTTP {response.status} on {path}")
        if not response.valid_json:
            raise UdiconnectApiError(f"Invalid JSON from {path}")
        return response.data

    async def async_sync_account(self) -> Any:
        """Return the raw SyncAccount payload."""
        return await self._async_request(_SYNC_ACCOUNT_PATH, {})

    async def async_get_devices(self) -> dict[str, UdiDevice]:
        """Return all devices keyed by device id."""
        return parse_devices(await self.async_sync_account())

    async def async_set_position(self, device_id: str, position: int) -> None:
        """Move a curtain to position (0 = closed, 100 = open)."""
        if not 0 <= position <= 100:
            raise ValueError("position must be between 0 and 100")
        # Never omit "position": the cloud defaults it to 0 and closes the curtain.
        await self._async_curtain_action(
            device_id, "CurtainSetPosition", {"position": int(position)}
        )

    async def async_stop(self, device_id: str) -> None:
        """Stop a moving curtain."""
        await self._async_curtain_action(device_id, "CurtainStop", {})

    async def _async_curtain_action(
        self, device_id: str, action: str, extra: dict[str, Any]
    ) -> None:
        payload = {"action": action, "deviceId": _cloud_device_id(device_id), **extra}
        data: Any = None
        for attempt in range(1, COMMAND_RETRIES + 1):
            data = await self._async_request(_CURTAIN_PATH, payload)
            if isinstance(data, dict) and _as_bool(data.get("result"), False):
                _LOGGER.debug("%s accepted for device %s", action, device_id)
                return
            _LOGGER.debug(
                "%s refused for device %s (attempt %s/%s): %s",
                action,
                device_id,
                attempt,
                COMMAND_RETRIES,
                data,
            )
            if attempt < COMMAND_RETRIES:
                await asyncio.sleep(COMMAND_RETRY_DELAY)
        raise UdiconnectApiError(f"{action} refused for device {device_id}: {data}")
