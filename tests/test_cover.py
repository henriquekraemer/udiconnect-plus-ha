"""Tests for the Udiconnect Plus cover platform."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import aiohttp
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    DOMAIN as COVER_DOMAIN,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_SET_COVER_POSITION,
    SERVICE_STOP_COVER,
    CoverState,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.udiconnect_plus.const import (
    DEFAULT_SCAN_INTERVAL,
    MOVE_FOLLOW_UP_SECONDS,
    MOVE_TIMEOUT_SECONDS,
)

from .conftest import SET_POSITION_URL, SYNC_URL, CloudMock, setup_integration

SALA = "cover.persiana_sala"
QUARTO = "cover.persiana_quarto"


async def _advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    """Advance time and run due timers."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_initial_state(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    sala = hass.states.get(SALA)
    assert sala.state == CoverState.OPEN
    assert sala.attributes[ATTR_CURRENT_POSITION] == 100
    assert sala.attributes["device_class"] == "blind"
    assert (
        sala.attributes["supported_features"] == 15
    )  # open, close, set position, stop
    assert sala.attributes["device_id"] == "101"
    assert sala.attributes["home_id"] == "71900"
    assert sala.attributes["home_name"] == "Casa"
    assert sala.attributes["category"] == "Blind"
    assert sala.attributes["is_calibrating"] is False
    assert (
        sala.attributes["friendly_name"] == "Persiana Sala"
    )  # not "Persiana Sala Persiana Sala"

    assert hass.states.get(QUARTO).state == STATE_UNAVAILABLE


@pytest.mark.parametrize(
    ("service", "data", "expected_position"),
    [
        (SERVICE_CLOSE_COVER, {}, 0),
        (SERVICE_OPEN_COVER, {}, 100),
        (SERVICE_SET_COVER_POSITION, {ATTR_POSITION: 35}, 35),
    ],
)
async def test_commands_send_position(
    hass: HomeAssistant,
    cloud: CloudMock,
    setup_entry: MockConfigEntry,
    service,
    data,
    expected_position,
) -> None:
    await hass.services.async_call(
        COVER_DOMAIN, service, {ATTR_ENTITY_ID: SALA, **data}, blocking=True
    )
    calls = cloud.calls_to(SET_POSITION_URL)
    assert len(calls) == 1
    assert calls[0][2] == {
        "action": "CurtainSetPosition",
        "deviceId": 101,
        "position": expected_position,
    }


async def test_close_reports_closing_until_cloud_confirms(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: CloudMock,
    setup_entry: MockConfigEntry,
) -> None:
    syncs_before = len(cloud.calls_to(SYNC_URL))

    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_CLOSE_COVER, {ATTR_ENTITY_ID: SALA}, blocking=True
    )
    assert hass.states.get(SALA).state == CoverState.CLOSING

    # debounced refresh after the command
    await _advance(hass, freezer, 3)
    assert len(cloud.calls_to(SYNC_URL)) == syncs_before + 1
    assert hass.states.get(SALA).state == CoverState.CLOSING

    cloud.device("101")["state"] = 0
    await _advance(hass, freezer, MOVE_FOLLOW_UP_SECONDS + 1)  # follow-up timer fires
    await _advance(hass, freezer, 3)  # ...and its debounced refresh runs
    assert len(cloud.calls_to(SYNC_URL)) == syncs_before + 2
    state = hass.states.get(SALA)
    assert state.state == CoverState.CLOSED
    assert state.attributes[ATTR_CURRENT_POSITION] == 0

    # no more follow-up polls
    await _advance(hass, freezer, MOVE_FOLLOW_UP_SECONDS + 1)
    await _advance(hass, freezer, 3)
    assert len(cloud.calls_to(SYNC_URL)) == syncs_before + 2


async def test_opening_state_and_timeout(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: CloudMock,
    setup_entry: MockConfigEntry,
) -> None:
    cloud.device("101")["state"] = 20
    await _advance(hass, freezer, DEFAULT_SCAN_INTERVAL + 1)
    assert hass.states.get(SALA).attributes[ATTR_CURRENT_POSITION] == 20

    await hass.services.async_call(
        COVER_DOMAIN,
        SERVICE_SET_COVER_POSITION,
        {ATTR_ENTITY_ID: SALA, ATTR_POSITION: 80},
        blocking=True,
    )
    assert hass.states.get(SALA).state == CoverState.OPENING

    for _ in range(3):
        await _advance(hass, freezer, MOVE_FOLLOW_UP_SECONDS + 1)
        await _advance(hass, freezer, 3)
        assert hass.states.get(SALA).state == CoverState.OPENING

    await _advance(hass, freezer, MOVE_TIMEOUT_SECONDS)
    await _advance(hass, freezer, 3)
    assert hass.states.get(SALA).state == CoverState.OPEN
    assert hass.states.get(SALA).attributes[ATTR_CURRENT_POSITION] == 20


async def test_stop_mid_movement(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: CloudMock,
    setup_entry: MockConfigEntry,
) -> None:
    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_CLOSE_COVER, {ATTR_ENTITY_ID: SALA}, blocking=True
    )
    assert hass.states.get(SALA).state == CoverState.CLOSING

    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_STOP_COVER, {ATTR_ENTITY_ID: SALA}, blocking=True
    )
    assert cloud.calls_to(SET_POSITION_URL)[-1][2] == {
        "action": "CurtainStop",
        "deviceId": 101,
    }
    assert hass.states.get(SALA).state == CoverState.OPEN

    cloud.device("101")["state"] = 56
    await _advance(hass, freezer, 3)
    state = hass.states.get(SALA)
    assert state.state == CoverState.OPEN
    assert state.attributes[ATTR_CURRENT_POSITION] == 56

    syncs = len(cloud.calls_to(SYNC_URL))
    await _advance(hass, freezer, MOVE_FOLLOW_UP_SECONDS + 1)
    await _advance(hass, freezer, 3)
    assert len(cloud.calls_to(SYNC_URL)) == syncs


async def test_stop_refused_raises(
    hass: HomeAssistant, cloud: CloudMock, setup_entry: MockConfigEntry
) -> None:
    cloud.set_body = {"result": False}
    with (
        patch("custom_components.udiconnect_plus.api.asyncio.sleep"),
        pytest.raises(HomeAssistantError, match="Persiana Sala"),
    ):
        await hass.services.async_call(
            COVER_DOMAIN, SERVICE_STOP_COVER, {ATTR_ENTITY_ID: SALA}, blocking=True
        )


async def test_command_refused_by_cloud_raises(
    hass: HomeAssistant, cloud: CloudMock, setup_entry: MockConfigEntry
) -> None:
    cloud.set_body = {"result": False, "message": "Busy"}
    with (
        patch("custom_components.udiconnect_plus.api.asyncio.sleep"),
        pytest.raises(HomeAssistantError, match="Persiana Sala"),
    ):
        await hass.services.async_call(
            COVER_DOMAIN, SERVICE_OPEN_COVER, {ATTR_ENTITY_ID: SALA}, blocking=True
        )
    assert hass.states.get(SALA).state == CoverState.OPEN


async def test_command_network_error_raises(
    hass: HomeAssistant, cloud: CloudMock, setup_entry: MockConfigEntry
) -> None:
    cloud.set_exc = aiohttp.ClientConnectionError("reset")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            COVER_DOMAIN, SERVICE_CLOSE_COVER, {ATTR_ENTITY_ID: SALA}, blocking=True
        )


async def test_command_relogins_on_expired_token(
    hass: HomeAssistant, cloud: CloudMock, setup_entry: MockConfigEntry
) -> None:
    cloud.rejected_tokens.add("token-1")
    cloud.login_body = {"accessToken": "token-2"}
    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_CLOSE_COVER, {ATTR_ENTITY_ID: SALA}, blocking=True
    )
    calls = cloud.calls_to(SET_POSITION_URL)
    assert [c[3]["access-token"] for c in calls] == ["token-1", "token-2"]


async def test_unavailable_when_poll_fails_and_recovers(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: CloudMock,
    setup_entry: MockConfigEntry,
) -> None:
    cloud.sync_status = 503
    await _advance(hass, freezer, DEFAULT_SCAN_INTERVAL + 1)
    assert hass.states.get(SALA).state == STATE_UNAVAILABLE

    cloud.sync_status = 200
    await _advance(hass, freezer, DEFAULT_SCAN_INTERVAL + 1)
    assert hass.states.get(SALA).state == CoverState.OPEN


async def test_unavailable_when_payload_degraded(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: CloudMock,
    setup_entry: MockConfigEntry,
) -> None:
    cloud.payload = {"account": {}}
    await _advance(hass, freezer, DEFAULT_SCAN_INTERVAL + 1)
    assert hass.states.get(SALA).state == STATE_UNAVAILABLE
    assert setup_entry.runtime_data.last_update_success is False


async def test_device_goes_online_and_offline(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: CloudMock,
    setup_entry: MockConfigEntry,
) -> None:
    cloud.device("102")["isOnline"] = True
    await _advance(hass, freezer, DEFAULT_SCAN_INTERVAL + 1)
    assert hass.states.get(QUARTO).state == CoverState.CLOSED

    cloud.device("101")["isOnline"] = False
    await _advance(hass, freezer, DEFAULT_SCAN_INTERVAL + 1)
    assert hass.states.get(SALA).state == STATE_UNAVAILABLE


async def test_position_unknown(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: CloudMock,
    setup_entry: MockConfigEntry,
) -> None:
    cloud.device("101")["state"] = None
    await _advance(hass, freezer, DEFAULT_SCAN_INTERVAL + 1)
    state = hass.states.get(SALA)
    assert state.state == "unknown"
    assert ATTR_CURRENT_POSITION not in state.attributes


async def test_new_device_is_added_later(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: CloudMock,
    setup_entry: MockConfigEntry,
) -> None:
    assert hass.states.get("cover.persiana_cozinha") is None
    cloud.payload["account"]["homeList"][0]["deviceList"].append(
        {
            "deviceId": 104,
            "description": "Persiana Cozinha",
            "state": 50,
            "controllerType": "Curtain",
        }
    )
    await _advance(hass, freezer, DEFAULT_SCAN_INTERVAL + 1)
    state = hass.states.get("cover.persiana_cozinha")
    assert state is not None
    assert state.attributes[ATTR_CURRENT_POSITION] == 50
    assert hass.states.get("binary_sensor.persiana_cozinha_connectivity").state == "on"


async def test_device_removed_from_account_becomes_unavailable(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: CloudMock,
    setup_entry: MockConfigEntry,
) -> None:
    cloud.payload["account"]["homeList"][0]["deviceList"].pop(0)
    await _advance(hass, freezer, DEFAULT_SCAN_INTERVAL + 1)
    assert hass.states.get(SALA).state == STATE_UNAVAILABLE


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("Blind", "blind"),
        ("Curtain", "curtain"),
        ("Shutter", "shutter"),
        ("Whatever", "blind"),
        (None, "blind"),
    ],
)
async def test_device_class_from_category(
    hass: HomeAssistant,
    cloud: CloudMock,
    mock_config_entry: MockConfigEntry,
    category,
    expected,
) -> None:
    cloud.device("101")["category"] = category
    assert await setup_integration(hass, mock_config_entry)
    assert hass.states.get(SALA).attributes["device_class"] == expected
