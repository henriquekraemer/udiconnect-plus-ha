"""Local UDP control and telemetry for Udiconnect Plus motors (UDIN-4208).

What is known about the protocol (decoded from a capture of one module's
cloud traffic and from its LAN broadcasts, November 2026):

* Every frame is ``[flag][source MAC x6][destination MAC x6][seq][body...]``.
  With flag ``00`` the body is plain. With flag ``01`` (the cloud's frames) the
  body, from byte 14 on, is XORed with a keystream that is fixed per module
  but differs between modules, so cloud frames are not reused here.
* The module broadcasts plain frames to ``255.255.255.255:20911``: a heartbeat
  (``ff 02``) every ~20 s, its position (``fd 04``, position in byte 19) every
  ~60 s and on every stop or arrival, and a text log line (``f5``) for each
  command it accepts or rejects ("Wrong command received: <byte 16>").
* Decoded, the cloud's command body is ``00 01 22 00`` for stop and
  ``00 01 27 <position>`` for move-to-position. Only these two are sent here,
  in plain (flag ``00``), like the Enumerate every module answers.

Commands are fire-and-forget UDP, so a send proves nothing. A command counts
as delivered only when the module broadcasts the log line that acknowledges
it, and for a move only when the target in that line is the one requested.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
import logging
import re
import socket
import time

_LOGGER = logging.getLogger(__name__)

PORT_COMMAND = 20910
PORT_TELEMETRY = 20911

_CMD_STOP = 0x22
_CMD_SET_POSITION = 0x27

_GROUP_LOG = 0xF5
_GROUP_POSITION = 0xFD
_GROUP_ENUMERATE = 0xA1

_BROADCAST_MAC = b"\xff" * 6
_ZERO_MAC = b"\x00" * 6

# Heartbeat every ~20 s and position every ~60 s: two missed position
# reports mean the module is not reachable on the LAN any more.
REACHABLE_SECONDS = 130
# A position heard in this window wins over the cloud's copy of it.
POSITION_FRESH_SECONDS = 300
# How often to look for either of the two running out.
LOCAL_CHECK_SECONDS = 10

# Wait for the acknowledging log line, then send once more before giving up.
CONFIRM_TIMEOUTS = (1.2, 1.8)

_RE_STOP_ACK = re.compile(r"stop command received", re.IGNORECASE)
_RE_MOVE_ACK = re.compile(r"move to position command received\D*(\d+)", re.IGNORECASE)


def normalize_mac(mac: str | None) -> str:
    """Return a MAC as 12 lowercase hex digits, or an empty string."""
    if not mac:
        return ""
    text = str(mac).lower().replace(":", "").replace("-", "").strip()
    if len(text) != 12 or any(c not in "0123456789abcdef" for c in text):
        return ""
    return text


def build_command_frame(mac: str, seq: int, command: int, param: int) -> bytes:
    """Build the 18-byte command frame in plain (flag 00)."""
    return (
        b"\x00"
        + _ZERO_MAC
        + bytes.fromhex(mac)
        + bytes([seq & 0xFF, 0x00, 0x01, command, param])
    )


def build_enumerate_frame(seq: int = 1) -> bytes:
    """Build the read-only Enumerate broadcast (answered with model,firmware)."""
    return b"\x00" + _ZERO_MAC + _BROADCAST_MAC + bytes([seq, 0x00, 0x00, 0xA1, 0x04])


@dataclass(frozen=True)
class CommandResult:
    """Outcome of a LAN command."""

    confirmed: bool
    attempts: int = 0
    seconds: float = 0.0
    # A newer command for the same motor replaced this one: the caller must not
    # resend it by any other route.
    superseded: bool = False


@dataclass
class _Pending:
    kind: str
    target: int | None
    # Resolves to True once acknowledged, False if replaced by a newer command.
    future: asyncio.Future[bool] = field(repr=False)
    other_target: int | None = None


@dataclass
class _ModuleState:
    ip: str
    last_seen: float
    position: int | None = None
    position_at: float = 0.0
    # Cleared by expire() once the entities have been told.
    online: bool = True
    position_fresh: bool = False


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, on_packet: Callable[[bytes, tuple[str, int]], None]) -> None:
        self._on_packet = on_packet

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._on_packet(data, addr)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("Local UDP socket error: %s", exc)


class UdiLocalManager:
    """Listen to the modules on the LAN and send them stop/move commands."""

    def __init__(self, on_change: Callable[[], None] | None = None) -> None:
        """Initialize. on_change runs in the event loop when state changes."""
        self._on_change = on_change
        self._modules: dict[str, _ModuleState] = {}
        # Only the motors of the account are tracked.
        self._macs: frozenset[str] = frozenset()
        # One command at a time per motor: the newest one.
        self._pending: dict[str, _Pending] = {}
        self._telemetry: asyncio.DatagramTransport | None = None
        self._command: asyncio.DatagramTransport | None = None
        self._seq = 0

    # ------------------------------------------------------------ lifecycle

    async def async_start(self) -> None:
        """Open the telemetry listener and the command socket, then enumerate."""
        loop = asyncio.get_running_loop()
        self._telemetry = await self._async_open(
            loop, PORT_TELEMETRY, self._handle_telemetry, "telemetry listener"
        )
        self._command = await self._async_open(
            loop, 0, self._handle_command_reply, "command socket"
        )
        if self._command is not None:
            # Replies arrive later through _handle_command_reply.
            self._command.sendto(
                build_enumerate_frame(), ("255.255.255.255", PORT_COMMAND)
            )

    def stop(self) -> None:
        """Close the sockets and fail any command still waiting."""
        for transport in (self._telemetry, self._command):
            if transport is not None:
                transport.close()
        self._telemetry = None
        self._command = None
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.cancel()
        self._pending.clear()

    @staticmethod
    async def _async_open(
        loop: asyncio.AbstractEventLoop,
        port: int,
        handler: Callable[[bytes, tuple[str, int]], None],
        what: str,
    ) -> asyncio.DatagramTransport | None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # SO_REUSEADDR alone lets a second listener (a test script, another
            # integration) share 20911, and every one of them gets the
            # broadcasts. SO_REUSEPORT is left out on purpose.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setblocking(False)
            # Broadcasts arrive on any interface, so bind to all of them.
            sock.bind(("0.0.0.0", port))  # noqa: S104
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _Protocol(handler), sock=sock
            )
        except OSError as err:
            sock.close()
            _LOGGER.warning("Local control: could not open the %s: %s", what, err)
            return None
        _LOGGER.debug("Local control: %s open on UDP %s", what, sock.getsockname()[1])
        return transport  # type: ignore[return-value]

    # ---------------------------------------------------------------- state

    @property
    def listening(self) -> bool:
        """Return True if the telemetry listener is open."""
        return self._telemetry is not None

    def set_macs(self, macs: Iterable[str | None]) -> None:
        """Set the motors to track; frames from any other sender are ignored."""
        self._macs = frozenset(filter(None, map(normalize_mac, macs)))
        for mac in self._modules.keys() - self._macs:
            del self._modules[mac]

    def is_reachable(self, mac: str | None) -> bool:
        """Return True if the module was heard on the LAN recently."""
        state = self._modules.get(normalize_mac(mac))
        return (
            state is not None and time.monotonic() - state.last_seen < REACHABLE_SECONDS
        )

    def position(self, mac: str | None) -> int | None:
        """Return the position the module last broadcast, if recent."""
        state = self._modules.get(normalize_mac(mac))
        if state is None or state.position is None:
            return None
        if time.monotonic() - state.position_at > POSITION_FRESH_SECONDS:
            return None
        return state.position

    def ip(self, mac: str | None) -> str | None:
        """Return the last IP seen for a module."""
        state = self._modules.get(normalize_mac(mac))
        return state.ip if state else None

    def expire(self) -> None:
        """Notify when a module goes quiet or its position gets stale.

        Nothing arrives to say so, so this has to be called periodically.
        """
        now = time.monotonic()
        expired = False
        for state in self._modules.values():
            if state.online and now - state.last_seen >= REACHABLE_SECONDS:
                state.online = False
                expired = True
            if (
                state.position_fresh
                and now - state.position_at > POSITION_FRESH_SECONDS
            ):
                state.position_fresh = False
                expired = True
        if expired:
            self._notify()

    # ------------------------------------------------------------- receive

    def _seen(self, mac: str, ip: str) -> _ModuleState | None:
        if mac not in self._macs:
            return None
        now = time.monotonic()
        state = self._modules.get(mac)
        if state is None:
            state = self._modules[mac] = _ModuleState(ip=ip, last_seen=now)
            _LOGGER.debug("Local control: module %s found at %s", mac, ip)
            self._notify()
        else:
            was_reachable = now - state.last_seen < REACHABLE_SECONDS
            state.ip = ip
            state.last_seen = now
            state.online = True
            if not was_reachable:
                self._notify()
        return state

    def _handle_telemetry(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) < 18 or data[0] != 0x00 or data[7:13] != _BROADCAST_MAC:
            return
        source = data[1:7]
        if source in (_ZERO_MAC, _BROADCAST_MAC):
            return
        mac = source.hex()
        state = self._seen(mac, addr[0])
        if state is None:
            return
        group = data[16]

        if group == _GROUP_POSITION and data[17] == 0x04 and len(data) >= 20:
            position = data[19]
            if position > 100:
                return
            now = time.monotonic()
            # After going stale, the same position as before is news again.
            changed = (
                position != state.position
                or now - state.position_at > POSITION_FRESH_SECONDS
            )
            state.position = position
            state.position_at = now
            state.position_fresh = True
            _LOGGER.debug("Local control: %s at %s%%", mac, position)
            if changed:
                self._notify()

        elif group == _GROUP_LOG:
            text = "".join(chr(b) for b in data[17:] if 32 <= b < 127).strip()
            _LOGGER.debug("Local control: log from %s: %s", mac, text)
            self._resolve(mac, text)

    def _handle_command_reply(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) >= 18 and data[0] == 0x00 and data[16] == _GROUP_ENUMERATE:
            mac = data[1:7].hex()
            if self._seen(mac, addr[0]) is not None:
                _LOGGER.debug(
                    "Local control: enumerate reply from %s (%s): %s",
                    mac,
                    addr[0],
                    data[18:].decode("ascii", "replace"),
                )
            return
        # Not used for anything yet: logged to learn whether the module also
        # acknowledges a LAN sender directly, as it does with the cloud.
        _LOGGER.debug("Local control: direct reply from %s: %s", addr, data.hex())

    def _resolve(self, mac: str, text: str) -> None:
        pending = self._pending.get(mac)
        if pending is None or pending.future.done():
            return
        if _RE_STOP_ACK.search(text):
            if pending.kind == "stop":
                pending.future.set_result(True)
        elif match := _RE_MOVE_ACK.search(text):
            if pending.kind != "move":
                return
            target = int(match.group(1))
            if target == pending.target:
                pending.future.set_result(True)
            else:
                # Usually the late acknowledgement of an earlier command: keep
                # waiting for our own.
                pending.other_target = target
        elif "wrong" in text.lower():
            # "Wrong command received: <n>": byte 16 is not a command this
            # firmware knows.
            _LOGGER.warning("Local control: %s rejected a command: %s", mac, text)

    def supersede(self, mac: str | None) -> None:
        """Give up on the command still waiting, because a newer one exists."""
        pending = self._pending.pop(normalize_mac(mac), None)
        if pending is not None and not pending.future.done():
            pending.future.set_result(False)

    def _notify(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:
                _LOGGER.exception("Local control: error in the change callback")

    # ---------------------------------------------------------------- send

    async def async_set_position(self, mac: str, position: int) -> CommandResult:
        """Move to position (0 closed, 100 open). Confirmed once the module acks."""
        position = max(0, min(100, int(position)))
        return await self._async_command(
            mac, "move", _CMD_SET_POSITION, position, position
        )

    async def async_stop(self, mac: str) -> CommandResult:
        """Stop the motor. Confirmed once the module acks."""
        return await self._async_command(mac, "stop", _CMD_STOP, 0x00, None)

    async def _async_command(
        self, mac: str, kind: str, command: int, param: int, target: int | None
    ) -> CommandResult:
        """Send, and send once more, until the module logs the command."""
        mac = normalize_mac(mac)
        # Resending an older command after this one would undo it.
        self.supersede(mac)
        ip = self.ip(mac)
        if not mac or ip is None or self._command is None or self._telemetry is None:
            _LOGGER.debug(
                "Local control: cannot send %s to %s (ip=%s, listening=%s)",
                kind,
                mac,
                ip,
                self.listening,
            )
            return CommandResult(False)

        loop = asyncio.get_running_loop()
        pending = _Pending(kind, target, loop.create_future())
        self._pending[mac] = pending
        started = time.monotonic()
        attempt = 0
        try:
            for attempt, timeout in enumerate(CONFIRM_TIMEOUTS, start=1):
                if pending.future.done():
                    break
                ip = self.ip(mac) or ip
                self._seq = (self._seq + 1) % 256
                frame = build_command_frame(mac, self._seq, command, param)
                self._command.sendto(frame, (ip, PORT_COMMAND))
                _LOGGER.debug(
                    "Local control: %s -> %s (%s) attempt %s: %s",
                    kind,
                    mac,
                    ip,
                    attempt,
                    frame.hex(),
                )
                try:
                    await asyncio.wait_for(asyncio.shield(pending.future), timeout)
                except TimeoutError:
                    continue
                except asyncio.CancelledError:
                    if pending.future.cancelled():
                        return CommandResult(False, attempt)
                    raise
                break
            seconds = time.monotonic() - started
            if pending.future.done():
                confirmed = pending.future.result()
                return CommandResult(
                    confirmed, attempt, seconds, superseded=not confirmed
                )
            if pending.other_target is not None:
                # Wrong encoding or a different firmware: the motor may be
                # heading to the wrong place.
                _LOGGER.error(
                    "Local control: %s acknowledged target %s%% instead of %s%%",
                    mac,
                    pending.other_target,
                    target,
                )
            return CommandResult(False, attempt, seconds)
        finally:
            if self._pending.get(mac) is pending:
                del self._pending[mac]
            if not pending.future.done():
                pending.future.cancel()
