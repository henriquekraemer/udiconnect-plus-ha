"""Tests for the control modes: cloud only, local with fallback, local only."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import timedelta
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.components.cover import (
    DOMAIN as COVER_DOMAIN,
    SERVICE_CLOSE_COVER,
    SERVICE_SET_COVER_POSITION,
    SERVICE_STOP_COVER,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.udiconnect_plus import local
from custom_components.udiconnect_plus.const import (
    CONF_CONTROL_MODE,
    MODE_CLOUD_ONLY,
    MODE_LOCAL_FALLBACK,
    MODE_LOCAL_ONLY,
)
from custom_components.udiconnect_plus.local import CommandResult, UdiLocalManager

from .conftest import SET_POSITION_URL, SYNC_URL, CloudMock, setup_integration
from .test_local import (
    ADDR,
    FakeTransport,
    _log_frame,
    _position_frame,
    _telemetry,
)

SALA = "cover.persiana_sala"
MAC = "aa:bb:cc:dd:ee:01"

_MANAGER = "custom_components.udiconnect_plus.coordinator.UdiLocalManager"


def _with_mode(entry: MockConfigEntry, hass: HomeAssistant, mode: str) -> None:
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={CONF_CONTROL_MODE: mode})


async def test_cloud_only_opens_nothing_local(
    hass: HomeAssistant, cloud: CloudMock, mock_config_entry: MockConfigEntry
) -> None:
    """The default mode behaves as before: no socket is opened."""
    assert CONF_CONTROL_MODE not in mock_config_entry.options
    with patch(f"{_MANAGER}.async_start", new=AsyncMock()) as start:
        assert await setup_integration(hass, mock_config_entry)

    start.assert_not_called()
    coordinator = mock_config_entry.runtime_data
    assert coordinator.control_mode == MODE_CLOUD_ONLY
    assert not coordinator.local_enabled
    assert not coordinator.local_manager.listening


@pytest.mark.parametrize("mode", [MODE_LOCAL_FALLBACK, MODE_LOCAL_ONLY])
async def test_local_modes_start_the_listener(
    hass: HomeAssistant,
    cloud: CloudMock,
    mock_config_entry: MockConfigEntry,
    mode: str,
) -> None:
    _with_mode(mock_config_entry, hass, mode)
    with patch(f"{_MANAGER}.async_start", new=AsyncMock()) as start:
        assert await setup_integration(hass, mock_config_entry)

    start.assert_awaited_once()
    assert mock_config_entry.runtime_data.local_enabled


@pytest.mark.parametrize(
    ("service", "data", "method", "args"),
    [
        (SERVICE_STOP_COVER, {}, "async_stop", (MAC,)),
        (SERVICE_CLOSE_COVER, {}, "async_set_position", (MAC, 0)),
        (SERVICE_SET_COVER_POSITION, {"position": 35}, "async_set_position", (MAC, 35)),
    ],
)
async def test_confirmed_command_skips_the_cloud(
    hass: HomeAssistant,
    cloud: CloudMock,
    mock_config_entry: MockConfigEntry,
    service,
    data,
    method,
    args,
) -> None:
    _with_mode(mock_config_entry, hass, MODE_LOCAL_FALLBACK)
    with (
        patch(f"{_MANAGER}.async_start", new=AsyncMock()),
        patch.object(UdiLocalManager, "is_reachable", return_value=True),
        patch(
            f"{_MANAGER}.{method}",
            new=AsyncMock(return_value=CommandResult(True, 1, 0.2)),
        ) as send,
    ):
        assert await setup_integration(hass, mock_config_entry)
        await hass.services.async_call(
            COVER_DOMAIN, service, {ATTR_ENTITY_ID: SALA, **data}, blocking=True
        )

    send.assert_awaited_once_with(*args)
    assert cloud.calls_to(SET_POSITION_URL) == []


async def test_unconfirmed_command_falls_back_to_the_cloud(
    hass: HomeAssistant,
    cloud: CloudMock,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _with_mode(mock_config_entry, hass, MODE_LOCAL_FALLBACK)
    with (
        patch(f"{_MANAGER}.async_start", new=AsyncMock()),
        patch.object(UdiLocalManager, "is_reachable", return_value=True),
        patch(
            f"{_MANAGER}.async_set_position",
            new=AsyncMock(return_value=CommandResult(False, 2, 3.0)),
        ),
        caplog.at_level(logging.WARNING),
    ):
        assert await setup_integration(hass, mock_config_entry)
        await hass.services.async_call(
            COVER_DOMAIN,
            SERVICE_CLOSE_COVER,
            {ATTR_ENTITY_ID: SALA},
            blocking=True,
        )

    calls = cloud.calls_to(SET_POSITION_URL)
    assert len(calls) == 1
    assert calls[0][2]["position"] == 0
    assert "not confirmed over the LAN" in caplog.text


async def test_local_only_never_uses_the_cloud(
    hass: HomeAssistant, cloud: CloudMock, mock_config_entry: MockConfigEntry
) -> None:
    _with_mode(mock_config_entry, hass, MODE_LOCAL_ONLY)
    with (
        patch(f"{_MANAGER}.async_start", new=AsyncMock()),
        patch(
            f"{_MANAGER}.async_set_position",
            new=AsyncMock(return_value=CommandResult(False, 2, 3.0)),
        ),
    ):
        assert await setup_integration(hass, mock_config_entry)
        with pytest.raises(HomeAssistantError, match="did not confirm"):
            await hass.services.async_call(
                COVER_DOMAIN,
                SERVICE_CLOSE_COVER,
                {ATTR_ENTITY_ID: SALA},
                blocking=True,
            )

    assert cloud.calls_to(SET_POSITION_URL) == []


async def test_cloud_only_ignores_the_lan(
    hass: HomeAssistant, cloud: CloudMock, mock_config_entry: MockConfigEntry
) -> None:
    with (
        patch(f"{_MANAGER}.async_set_position", new=AsyncMock()) as local_move,
        patch.object(UdiLocalManager, "position", return_value=7),
    ):
        assert await setup_integration(hass, mock_config_entry)
        await hass.services.async_call(
            COVER_DOMAIN,
            SERVICE_CLOSE_COVER,
            {ATTR_ENTITY_ID: SALA},
            blocking=True,
        )

    local_move.assert_not_called()
    assert len(cloud.calls_to(SET_POSITION_URL)) == 1
    assert hass.states.get(SALA).attributes["current_position"] == 100


async def test_lan_position_wins_over_the_cloud_copy(
    hass: HomeAssistant, cloud: CloudMock, mock_config_entry: MockConfigEntry
) -> None:
    _with_mode(mock_config_entry, hass, MODE_LOCAL_FALLBACK)
    with (
        patch(f"{_MANAGER}.async_start", new=AsyncMock()),
        patch.object(UdiLocalManager, "position", return_value=42),
    ):
        assert await setup_integration(hass, mock_config_entry)

    assert hass.states.get(SALA).attributes["current_position"] == 42


async def test_changing_the_mode_reloads_the_entry(
    hass: HomeAssistant, cloud: CloudMock, mock_config_entry: MockConfigEntry
) -> None:
    assert await setup_integration(hass, mock_config_entry)
    first = mock_config_entry.runtime_data
    assert not first.local_enabled

    with patch(f"{_MANAGER}.async_start", new=AsyncMock()) as start:
        hass.config_entries.async_update_entry(
            mock_config_entry, options={CONF_CONTROL_MODE: MODE_LOCAL_FALLBACK}
        )
        await hass.async_block_till_done()

    assert mock_config_entry.runtime_data is not first
    assert mock_config_entry.runtime_data.local_enabled
    start.assert_awaited_once()


async def test_motor_not_heard_goes_straight_to_the_cloud(
    hass: HomeAssistant,
    cloud: CloudMock,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _with_mode(mock_config_entry, hass, MODE_LOCAL_FALLBACK)
    with (
        patch(f"{_MANAGER}.async_start", new=AsyncMock()),
        patch(f"{_MANAGER}.async_set_position", new=AsyncMock()) as local_move,
        caplog.at_level(logging.WARNING),
    ):
        assert await setup_integration(hass, mock_config_entry)
        for _ in range(2):
            await hass.services.async_call(
                COVER_DOMAIN,
                SERVICE_CLOSE_COVER,
                {ATTR_ENTITY_ID: SALA},
                blocking=True,
            )

    local_move.assert_not_called()
    assert len(cloud.calls_to(SET_POSITION_URL)) == 2
    # Warned once, not on every command.
    assert caplog.text.count("not confirmed over the LAN") == 1


async def test_replaced_command_is_not_sent_through_the_cloud(
    hass: HomeAssistant, cloud: CloudMock, mock_config_entry: MockConfigEntry
) -> None:
    _with_mode(mock_config_entry, hass, MODE_LOCAL_FALLBACK)
    with (
        patch(f"{_MANAGER}.async_start", new=AsyncMock()),
        patch.object(UdiLocalManager, "is_reachable", return_value=True),
        patch(
            f"{_MANAGER}.async_set_position",
            new=AsyncMock(return_value=CommandResult(False, 1, 0.5, superseded=True)),
        ),
    ):
        assert await setup_integration(hass, mock_config_entry)
        await hass.services.async_call(
            COVER_DOMAIN,
            SERVICE_CLOSE_COVER,
            {ATTR_ENTITY_ID: SALA},
            blocking=True,
        )

    assert cloud.calls_to(SET_POSITION_URL) == []


# The tests below feed frames to the real manager: only the sockets and its
# clock are replaced.


class _Lan:
    """A motor on a fake LAN, with the manager's clock under test control."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.now = 1000.0
        self.elapsed = 0.0
        self.telemetry = FakeTransport()
        self.command = FakeTransport(self._answer)
        self.manager: UdiLocalManager | None = None

    def _answer(self, _count: int, data: bytes) -> None:
        """Acknowledge stop and move like the motor does."""
        if data[16] == 0x22:
            text = "Stop command received"
        elif data[16] == 0x27:
            text = f"Move to position command received {data[17]}"
        else:
            return
        asyncio.get_running_loop().call_soon(self.send, _log_frame(text))

    def send(self, frame: bytes) -> None:
        self.manager._handle_telemetry(frame, ADDR)

    async def advance(self, seconds: float) -> None:
        """Move both the manager's clock and Home Assistant's timers."""
        self.now += seconds
        self.elapsed += seconds
        async_fire_time_changed(
            self.hass, dt_util.utcnow() + timedelta(seconds=self.elapsed)
        )
        await self.hass.async_block_till_done()


@pytest.fixture
def lan(hass: HomeAssistant) -> Generator[_Lan]:
    fake = _Lan(hass)
    with (
        patch.object(local, "time", SimpleNamespace(monotonic=lambda: fake.now)),
        patch.object(
            UdiLocalManager,
            "_async_open",
            new=AsyncMock(side_effect=[fake.telemetry, fake.command]),
        ),
    ):
        yield fake


async def _setup_lan(
    hass: HomeAssistant, entry: MockConfigEntry, lan: _Lan, position: int = 100
) -> None:
    _with_mode(entry, hass, MODE_LOCAL_FALLBACK)
    assert await setup_integration(hass, entry)
    lan.manager = entry.runtime_data.local_manager
    lan.send(_position_frame(position))
    await hass.async_block_till_done()


async def _close(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_CLOSE_COVER, {ATTR_ENTITY_ID: SALA}, blocking=True
    )


async def test_motor_going_quiet_with_the_cloud_down(
    hass: HomeAssistant,
    cloud: CloudMock,
    mock_config_entry: MockConfigEntry,
    lan: _Lan,
) -> None:
    await _setup_lan(hass, mock_config_entry, lan)
    coordinator = mock_config_entry.runtime_data
    cloud.sync_exc = TimeoutError()
    await coordinator.async_refresh()
    # A second failed poll does not notify the entities.
    await coordinator.async_refresh()
    assert not coordinator.last_update_success

    await lan.advance(60)
    lan.send(_position_frame(100))
    assert hass.states.get(SALA).state == "open"
    await _close(hass)
    assert cloud.calls_to(SET_POSITION_URL) == []

    await lan.advance(local.REACHABLE_SECONDS + 1)

    assert hass.states.get(SALA).state == "unavailable"


async def test_lan_move_is_followed_without_the_cloud(
    hass: HomeAssistant,
    cloud: CloudMock,
    mock_config_entry: MockConfigEntry,
    lan: _Lan,
) -> None:
    await _setup_lan(hass, mock_config_entry, lan)
    polls = len(cloud.calls_to(SYNC_URL))

    await _close(hass)
    assert hass.states.get(SALA).state == "closing"
    await lan.advance(6)  # the follow-up timer
    assert len(cloud.calls_to(SYNC_URL)) == polls
    assert hass.states.get(SALA).state == "closing"

    lan.send(_position_frame(0))
    await hass.async_block_till_done()

    assert hass.states.get(SALA).state == "closed"
    assert len(cloud.calls_to(SYNC_URL)) == polls


async def test_position_broadcast_updates_the_cover(
    hass: HomeAssistant,
    cloud: CloudMock,
    mock_config_entry: MockConfigEntry,
    lan: _Lan,
) -> None:
    await _setup_lan(hass, mock_config_entry, lan)
    polls = len(cloud.calls_to(SYNC_URL))

    lan.send(_position_frame(40))
    await hass.async_block_till_done()

    assert hass.states.get(SALA).attributes["current_position"] == 40
    assert len(cloud.calls_to(SYNC_URL)) == polls


async def test_stale_position_gives_way_and_comes_back(
    hass: HomeAssistant,
    cloud: CloudMock,
    mock_config_entry: MockConfigEntry,
    lan: _Lan,
) -> None:
    await _setup_lan(hass, mock_config_entry, lan, position=40)
    cloud.sync_exc = TimeoutError()
    await mock_config_entry.runtime_data.async_refresh()
    await mock_config_entry.runtime_data.async_refresh()

    # Heartbeats keep arriving, position reports do not.
    for _ in range(4):
        await lan.advance(local.POSITION_FRESH_SECONDS / 3)
        lan.send(_telemetry(0xFF, b"\x02UDIN-4208"))
    await lan.advance(local.LOCAL_CHECK_SECONDS)
    # Back to the last position the cloud reported.
    assert hass.states.get(SALA).attributes["current_position"] == 100

    # The same position as before is news again.
    lan.send(_position_frame(40))
    await hass.async_block_till_done()
    assert hass.states.get(SALA).attributes["current_position"] == 40
