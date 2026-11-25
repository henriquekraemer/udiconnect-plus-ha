"""Tests for the local UDP engine."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from custom_components.udiconnect_plus import local
from custom_components.udiconnect_plus.local import (
    UdiLocalManager,
    build_command_frame,
    build_enumerate_frame,
    normalize_mac,
)

MAC = "aa:bb:cc:dd:ee:01"
MAC_HEX = "aabbccddee01"
ADDR = ("192.0.2.10", local.PORT_TELEMETRY)
BROADCAST = b"\xff" * 6


def _telemetry(group: int, body: bytes, mac_hex: str = MAC_HEX) -> bytes:
    """Build a plain frame a module broadcasts."""
    return b"\x00" + bytes.fromhex(mac_hex) + BROADCAST + bytes([1, 0, 0, group]) + body


def _position_frame(position: int, mac_hex: str = MAC_HEX) -> bytes:
    return _telemetry(0xFD, bytes([0x04, 0x00, position]), mac_hex)


def _log_frame(text: str, mac_hex: str = MAC_HEX) -> bytes:
    return _telemetry(0xF5, text.encode("ascii"), mac_hex)


class FakeTransport:
    """Records datagrams and answers them like a module would."""

    def __init__(self, on_send=None) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.on_send = on_send
        self.closed = False

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((data, addr))
        if self.on_send is not None:
            self.on_send(len(self.sent), data)

    def close(self) -> None:
        self.closed = True


def _tracking(on_change=None) -> UdiLocalManager:
    """Return a manager that tracks the test module."""
    manager = UdiLocalManager(on_change)
    manager.set_macs([MAC])
    return manager


def _manager(on_send=None) -> tuple[UdiLocalManager, FakeTransport]:
    """Return a manager that already heard the module, without real sockets."""
    manager = _tracking()
    transport = FakeTransport(on_send)
    manager._command = transport
    manager._telemetry = FakeTransport()
    manager._handle_telemetry(_position_frame(100), ADDR)
    return manager, transport


def test_normalize_mac() -> None:
    assert normalize_mac("AA:BB:CC:DD:EE:01") == MAC_HEX
    assert normalize_mac("aa-bb-cc-dd-ee-01") == MAC_HEX
    assert normalize_mac("aabbccddee01") == MAC_HEX
    assert normalize_mac(None) == ""
    assert normalize_mac("not a mac") == ""
    assert normalize_mac("aa:bb:cc:dd:ee") == ""


def test_stop_frame() -> None:
    frame = build_command_frame(MAC_HEX, 5, 0x22, 0x00)
    assert frame == bytes.fromhex("00" + "000000000000" + MAC_HEX + "05" + "00012200")
    assert len(frame) == 18


@pytest.mark.parametrize(
    ("position", "expected"), [(0, 0x00), (25, 0x19), (38, 0x26), (100, 0x64)]
)
def test_move_frame_carries_the_position_as_is(position: int, expected: int) -> None:
    frame = build_command_frame(MAC_HEX, 300, 0x27, position)
    assert frame[13] == 300 & 0xFF
    assert frame[14:17] == b"\x00\x01\x27"
    assert frame[17] == expected


def test_enumerate_frame_is_a_read_only_broadcast() -> None:
    frame = build_enumerate_frame()
    assert frame[7:13] == BROADCAST
    assert frame[16] == 0xA1


def test_position_and_presence_from_broadcasts() -> None:
    changes: list[int] = []
    manager = _tracking(lambda: changes.append(1))

    assert not manager.is_reachable(MAC)
    assert manager.position(MAC) is None

    manager._handle_telemetry(_position_frame(38), ADDR)

    assert manager.is_reachable(MAC)
    assert manager.position(MAC) == 38
    assert manager.ip(MAC) == ADDR[0]
    assert changes  # found, then its position changed

    changes.clear()
    manager._handle_telemetry(_position_frame(38), ADDR)
    assert not changes  # same position, nothing to tell


def test_position_and_presence_go_stale() -> None:
    manager = _tracking()
    manager._handle_telemetry(_position_frame(60), ADDR)
    state = manager._modules[MAC_HEX]

    state.position_at -= local.POSITION_FRESH_SECONDS + 1
    assert manager.position(MAC) is None
    assert manager.is_reachable(MAC)

    state.last_seen -= local.REACHABLE_SECONDS + 1
    assert not manager.is_reachable(MAC)


def test_expire_reports_each_loss_once() -> None:
    changes: list[int] = []
    manager = _tracking(lambda: changes.append(1))
    manager._handle_telemetry(_position_frame(60), ADDR)
    state = manager._modules[MAC_HEX]
    changes.clear()

    manager.expire()
    assert not changes

    state.position_at -= local.POSITION_FRESH_SECONDS + 1
    manager.expire()
    manager.expire()
    assert len(changes) == 1

    state.last_seen -= local.REACHABLE_SECONDS + 1
    manager.expire()
    manager.expire()
    assert len(changes) == 2


@pytest.mark.parametrize(
    "frame",
    [
        b"\x01" + bytes.fromhex(MAC_HEX) + BROADCAST + bytes([1, 0, 0, 0xFD, 4, 0, 50]),
        _position_frame(50, "000000000000"),
        _position_frame(50, "ffffffffffff"),
        b"\x00\x01\x02",
    ],
)
def test_bad_frames_are_ignored(frame: bytes) -> None:
    manager = _tracking()
    manager._handle_telemetry(frame, ADDR)
    assert manager.position(MAC) is None
    assert not manager.is_reachable(MAC)


def test_position_above_100_is_not_used() -> None:
    manager = _tracking()
    manager._handle_telemetry(_position_frame(101), ADDR)
    assert manager.position(MAC) is None


async def test_unknown_module_sends_nothing() -> None:
    manager, transport = _manager()
    result = await manager.async_stop("aa:bb:cc:dd:ee:99")
    assert not result.confirmed
    assert transport.sent == []


async def test_stop_is_confirmed_by_the_log_line() -> None:
    manager = _tracking()

    def answer(_count: int, _data: bytes) -> None:
        asyncio.get_running_loop().call_soon(
            manager._handle_telemetry, _log_frame("Stop command received"), ADDR
        )

    transport = FakeTransport(answer)
    manager._command = transport
    manager._telemetry = FakeTransport()
    manager._handle_telemetry(_position_frame(100), ADDR)

    result = await manager.async_stop(MAC)

    assert result.confirmed
    assert result.attempts == 1
    assert transport.sent[0][1] == (ADDR[0], local.PORT_COMMAND)
    assert transport.sent[0][0][14:18] == b"\x00\x01\x22\x00"


async def test_move_is_confirmed_when_the_target_is_echoed() -> None:
    manager = _tracking()

    def answer(_count: int, _data: bytes) -> None:
        asyncio.get_running_loop().call_soon(
            manager._handle_telemetry,
            _log_frame("Move to position command received 25"),
            ADDR,
        )

    manager._command = FakeTransport(answer)
    manager._telemetry = FakeTransport()
    manager._handle_telemetry(_position_frame(100), ADDR)

    result = await manager.async_set_position(MAC, 25)

    assert result.confirmed
    assert result.attempts == 1


async def test_move_with_another_target_is_not_confirmed() -> None:
    manager = _tracking()

    def answer(_count: int, _data: bytes) -> None:
        asyncio.get_running_loop().call_soon(
            manager._handle_telemetry,
            _log_frame("Move to position command received 43"),
            ADDR,
        )

    manager._command = FakeTransport(answer)
    manager._telemetry = FakeTransport()
    manager._handle_telemetry(_position_frame(100), ADDR)

    result = await manager.async_set_position(MAC, 25)

    assert not result.confirmed


async def test_command_is_resent_once_then_gives_up() -> None:
    manager, transport = _manager()
    with patch.object(local, "CONFIRM_TIMEOUTS", (0.01, 0.01)):
        result = await manager.async_stop(MAC)

    assert not result.confirmed
    assert result.attempts == 2
    assert len(transport.sent) == 2
    assert not manager._pending


async def test_second_attempt_can_succeed() -> None:
    manager = _tracking()

    def answer(count: int, _data: bytes) -> None:
        if count == 2:
            asyncio.get_running_loop().call_soon(
                manager._handle_telemetry, _log_frame("Stop command received"), ADDR
            )

    manager._command = FakeTransport(answer)
    manager._telemetry = FakeTransport()
    manager._handle_telemetry(_position_frame(100), ADDR)

    with patch.object(local, "CONFIRM_TIMEOUTS", (0.05, 0.5)):
        result = await manager.async_stop(MAC)

    assert result.confirmed
    assert result.attempts == 2


async def test_stop_closes_the_sockets_and_fails_waiting_commands() -> None:
    manager, transport = _manager()
    telemetry = manager._telemetry
    task = asyncio.create_task(manager.async_stop(MAC))
    await asyncio.sleep(0)

    manager.stop()
    result = await task

    assert not result.confirmed
    assert transport.closed
    assert telemetry.closed
    assert not manager.listening


def test_unknown_senders_are_not_tracked() -> None:
    changes: list[int] = []
    manager = _tracking(lambda: changes.append(1))

    manager._handle_telemetry(_position_frame(50, "aabbccddee99"), ADDR)

    assert not manager._modules
    assert not changes


async def test_newer_command_replaces_the_waiting_one() -> None:
    manager, transport = _manager()
    with patch.object(local, "CONFIRM_TIMEOUTS", (0.05, 0.05)):
        first = asyncio.create_task(manager.async_set_position(MAC, 25))
        await asyncio.sleep(0)
        second = asyncio.create_task(manager.async_set_position(MAC, 80))
        old = await first
        # Late acknowledgement of the first move, then the one of the second.
        manager._handle_telemetry(
            _log_frame("Move to position command received 25"), ADDR
        )
        manager._handle_telemetry(
            _log_frame("Move to position command received 80"), ADDR
        )
        new = await second

    assert old.superseded
    assert not old.confirmed
    assert new.confirmed
    assert not new.superseded
    # The older target is never sent again after the newer one.
    assert [frame[17] for frame, _ in transport.sent] == [25, 80]


async def test_stop_replaces_a_waiting_move() -> None:
    manager, transport = _manager()
    with patch.object(local, "CONFIRM_TIMEOUTS", (0.01, 0.01)):
        move = asyncio.create_task(manager.async_set_position(MAC, 0))
        await asyncio.sleep(0)
        stop = await manager.async_stop(MAC)

    assert (await move).superseded
    assert not stop.superseded
    assert [frame[16] for frame, _ in transport.sent] == [0x27, 0x22, 0x22]
