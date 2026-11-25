"""Cover platform for Udiconnect Plus."""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .api import UdiconnectError
from .const import (
    ATTR_CATEGORY,
    ATTR_DEVICE_ID,
    ATTR_HOME_ID,
    ATTR_HOME_NAME,
    ATTR_IS_CALIBRATING,
    DOMAIN,
    MODE_CLOUD_ONLY,
    MODE_LOCAL_ONLY,
    MOVE_FOLLOW_UP_SECONDS,
    MOVE_TIMEOUT_SECONDS,
)
from .coordinator import UdiconnectConfigEntry, UdiconnectCoordinator
from .entity import UdiconnectEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

DEVICE_CLASSES = {
    "blind": CoverDeviceClass.BLIND,
    "curtain": CoverDeviceClass.CURTAIN,
    "shutter": CoverDeviceClass.SHUTTER,
    "shade": CoverDeviceClass.SHADE,
    "awning": CoverDeviceClass.AWNING,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UdiconnectConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Udiconnect Plus covers."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _async_add_new_covers() -> None:
        new_keys: list[str] = []
        for device_key, device in coordinator.data.items():
            if device_key in known:
                continue
            known.add(device_key)
            if device.is_cover:
                new_keys.append(device_key)
            else:
                _LOGGER.info(
                    "Skipping device %s (%s): controllerType=%r category=%r",
                    device.name,
                    device.device_id,
                    device.controller_type,
                    device.category,
                )
        if new_keys:
            async_add_entities(
                UdiconnectCover(coordinator, device_key) for device_key in new_keys
            )

    _async_add_new_covers()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_covers))


class UdiconnectCover(UdiconnectEntity, CoverEntity):
    """Udiconnect Plus blind or curtain."""

    _attr_name = None
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.SET_POSITION
        | CoverEntityFeature.STOP
    )

    def __init__(self, coordinator: UdiconnectCoordinator, device_key: str) -> None:
        """Initialize the cover."""
        super().__init__(coordinator, device_key)
        self._attr_unique_id = device_key
        category = (coordinator.data[device_key].category or "").lower()
        self._attr_device_class = DEVICE_CLASSES.get(category, CoverDeviceClass.BLIND)
        self._target_position: int | None = None
        self._move_started_at: float | None = None
        self._unsub_follow_up: CALLBACK_TYPE | None = None
        self._lan_failure_logged = False

    @property
    def _control_mode(self) -> str:
        return self.coordinator.control_mode

    def _local_mac(self) -> str | None:
        """MAC to use on the LAN, or None in cloud-only mode."""
        device = self.device
        if device is None or self._control_mode == MODE_CLOUD_ONLY:
            return None
        return device.mac_address

    def _local_reachable(self) -> bool:
        mac = self._local_mac()
        return mac is not None and self.coordinator.local_manager.is_reachable(mac)

    @property
    def available(self) -> bool:
        """Return True if the device answers on the LAN or the cloud says online.

        Home Assistant skips service calls to unavailable entities, so tying
        this to the cloud alone would block local control exactly when the
        cloud fails.
        """
        device = self.device
        if device is None:
            return False
        if self._local_reachable():
            return True
        return super().available and device.is_online

    @property
    def current_cover_position(self) -> int | None:
        """Return the current position (0 closed, 100 open)."""
        device = self.device
        if device is None:
            return None
        mac = self._local_mac()
        if mac is not None:
            local = self.coordinator.local_manager.position(mac)
            if local is not None:
                return local
        return device.position

    @property
    def is_closed(self) -> bool | None:
        """Return True if the cover is closed."""
        position = self.current_cover_position
        return None if position is None else position == 0

    @property
    def is_opening(self) -> bool:
        """Return True if the cover is opening."""
        return self._moving_direction() == "opening"

    @property
    def is_closing(self) -> bool:
        """Return True if the cover is closing."""
        return self._moving_direction() == "closing"

    def _moving_direction(self) -> str | None:
        if self._target_position is None:
            return None
        current = self.current_cover_position
        if current is None or current == self._target_position:
            return None
        return "opening" if self._target_position > current else "closing"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        device = self.device
        if device is None:
            return {}
        return {
            ATTR_DEVICE_ID: device.device_id,
            ATTR_HOME_ID: device.home_id,
            ATTR_HOME_NAME: device.home_name,
            ATTR_CATEGORY: device.category,
            ATTR_IS_CALIBRATING: device.is_calibrating,
        }

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        await self._async_set_position(100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        await self._async_set_position(0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position."""
        await self._async_set_position(int(kwargs[ATTR_POSITION]))

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        sent_locally = await self._async_command("stop")
        if sent_locally is None:
            return
        self._stop_tracking()
        self.async_write_ha_state()
        if not sent_locally:
            # Over the LAN the module broadcasts where it stopped by itself.
            await self.coordinator.async_request_refresh()

    async def _async_set_position(self, position: int) -> None:
        sent_locally = await self._async_command("move", position)
        if sent_locally is None:
            return

        # The cloud keeps reporting the old position until the motor arrives,
        # so show opening/closing meanwhile. Over the LAN the module
        # broadcasts its position on arrival.
        self._target_position = position
        self._move_started_at = time.monotonic()
        self.async_write_ha_state()
        self._schedule_follow_up()
        if not sent_locally:
            await self.coordinator.async_request_refresh()

    async def _async_command(self, kind: str, position: int = 0) -> bool | None:
        """Send stop or move per the control mode.

        Return True if it went over the LAN, False if through the cloud, and
        None if a newer command replaced it while it was waiting.

        A LAN command counts only when the module acknowledges it, so the
        cloud fallback runs when the module did not hear it, not merely when
        the datagram could not be sent. Both commands are absolute (stop, go
        to N%), so a command that reached the module and is resent through
        the cloud does no harm.
        """
        what = "stop" if kind == "stop" else f"move to {position}%"
        mode = self._control_mode
        if mode != MODE_CLOUD_ONLY:
            mac = self._local_mac()
            local = self.coordinator.local_manager
            if mac is not None:
                if mode == MODE_LOCAL_ONLY or local.is_reachable(mac):
                    if kind == "stop":
                        result = await local.async_stop(mac)
                    else:
                        result = await local.async_set_position(mac, position)
                    if result.superseded:
                        return None
                    if result.confirmed:
                        self._lan_failure_logged = False
                        _LOGGER.debug(
                            "%s: %s over the LAN, confirmed in %.1f s (attempt %d)",
                            self.entity_id,
                            what,
                            result.seconds,
                            result.attempts,
                        )
                        return True
                else:
                    # Not heard lately: do not make the user wait for it.
                    local.supersede(mac)
            if mode == MODE_LOCAL_ONLY:
                device = self.device
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="local_failed",
                    translation_placeholders={
                        "name": device.name if device else self.entity_id
                    },
                )
            # Once per motor until the LAN works again.
            _LOGGER.log(
                logging.DEBUG if self._lan_failure_logged else logging.WARNING,
                "%s: %s not confirmed over the LAN, sending it through the cloud",
                self.entity_id,
                what,
            )
            self._lan_failure_logged = True

        try:
            if kind == "stop":
                await self.coordinator.client.async_stop(self._cloud_device_id())
            else:
                await self.coordinator.client.async_set_position(
                    self._cloud_device_id(), position
                )
        except UdiconnectError as err:
            key = "stop_failed" if kind == "stop" else "set_position_failed"
            raise self._command_error(key, err) from err
        _LOGGER.debug("%s: %s through the cloud, accepted", self.entity_id, what)
        return False

    def _cloud_device_id(self) -> str:
        # The cloud renumbers deviceId, so take it from the latest poll.
        device = self.device
        if device is None:
            raise UdiconnectError("device is no longer reported by the cloud")
        return device.device_id

    def _command_error(self, key: str, err: Exception) -> HomeAssistantError:
        device = self.device
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=key,
            translation_placeholders={
                "name": device.name if device else self.entity_id,
                "error": str(err),
            },
        )

    def _schedule_follow_up(self) -> None:
        self._cancel_follow_up()
        self._unsub_follow_up = async_call_later(
            self.hass, MOVE_FOLLOW_UP_SECONDS, self._async_follow_up
        )

    def _cancel_follow_up(self) -> None:
        if self._unsub_follow_up is not None:
            self._unsub_follow_up()
            self._unsub_follow_up = None

    async def _async_follow_up(self, _now: Any) -> None:
        self._unsub_follow_up = None
        if self._target_position is None:
            return
        if self._local_reachable():
            # Arrival comes as a LAN broadcast: only check the timeout here,
            # without polling the cloud every few seconds.
            self._handle_coordinator_update()
        else:
            await self.coordinator.async_request_refresh()

    def _stop_tracking(self) -> None:
        self._target_position = None
        self._move_started_at = None
        self._cancel_follow_up()

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._target_position is not None:
            reached = self.current_cover_position == self._target_position
            timed_out = (
                self._move_started_at is not None
                and time.monotonic() - self._move_started_at > MOVE_TIMEOUT_SECONDS
            )
            if reached or timed_out or not self.available:
                if timed_out:
                    _LOGGER.debug(
                        "%s did not reach position %s within %ss",
                        self.entity_id,
                        self._target_position,
                        MOVE_TIMEOUT_SECONDS,
                    )
                self._stop_tracking()
            else:
                self._schedule_follow_up()
        super()._handle_coordinator_update()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel pending timers."""
        self._cancel_follow_up()
        await super().async_will_remove_from_hass()
