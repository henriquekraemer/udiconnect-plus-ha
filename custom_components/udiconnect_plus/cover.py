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
    MOVE_FOLLOW_UP_SECONDS,
    MOVE_TIMEOUT_SECONDS,
)
from .coordinator import UdiconnectConfigEntry, UdiconnectCoordinator
from .entity import UdiconnectEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


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
        new_ids: list[str] = []
        for device_id, device in coordinator.data.items():
            if device_id in known:
                continue
            known.add(device_id)
            if device.is_cover:
                new_ids.append(device_id)
            else:
                _LOGGER.info(
                    "Skipping device %s (%s): controllerType=%r category=%r",
                    device.name,
                    device_id,
                    device.controller_type,
                    device.category,
                )
        if new_ids:
            async_add_entities(
                UdiconnectCover(coordinator, device_id) for device_id in new_ids
            )

    _async_add_new_covers()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_covers))


class UdiconnectCover(UdiconnectEntity, CoverEntity):
    """Udiconnect Plus blind or curtain."""

    _attr_name = None
    _attr_device_class = CoverDeviceClass.BLIND
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.SET_POSITION
        | CoverEntityFeature.STOP
    )

    def __init__(self, coordinator: UdiconnectCoordinator, device_id: str) -> None:
        """Initialize the cover."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = device_id
        self._target_position: int | None = None
        self._move_started_at: float | None = None
        self._unsub_follow_up: CALLBACK_TYPE | None = None

    @property
    def available(self) -> bool:
        """Return True if the device is online."""
        device = self.device
        return super().available and device is not None and device.is_online

    @property
    def current_cover_position(self) -> int | None:
        """Return the current position (0 closed, 100 open)."""
        device = self.device
        return device.position if device else None

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
        try:
            await self.coordinator.client.async_stop(self._device_id)
        except UdiconnectError as err:
            raise self._command_error("stop_failed", err) from err

        self._stop_tracking()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def _async_set_position(self, position: int) -> None:
        try:
            await self.coordinator.client.async_set_position(self._device_id, position)
        except UdiconnectError as err:
            raise self._command_error("set_position_failed", err) from err

        # The cloud keeps reporting the old position until the motor arrives,
        # so poll faster until then and show opening/closing in the meantime.
        self._target_position = position
        self._move_started_at = time.monotonic()
        self.async_write_ha_state()
        self._schedule_follow_up()
        await self.coordinator.async_request_refresh()

    def _command_error(self, key: str, err: Exception) -> HomeAssistantError:
        device = self.device
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=key,
            translation_placeholders={
                "name": device.name if device else self._device_id,
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
        if self._target_position is not None:
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
