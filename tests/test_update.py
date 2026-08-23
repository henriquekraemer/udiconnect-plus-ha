"""Tests for the Udiconnect Plus update platform."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.update import ATTR_INSTALLED_VERSION, ATTR_LATEST_VERSION
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.udiconnect_plus.const import DEFAULT_SCAN_INTERVAL

from .conftest import CloudMock


async def test_update_entities(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    sala = hass.states.get("update.persiana_sala_firmware")
    assert sala.state == STATE_OFF
    assert sala.attributes[ATTR_INSTALLED_VERSION] == "2.1.3.0"
    assert sala.attributes[ATTR_LATEST_VERSION] == "2.1.3.0"
    assert sala.attributes["device_class"] == "firmware"
    assert sala.attributes["supported_features"] == 0

    quarto = hass.states.get("update.persiana_quarto_firmware")
    assert quarto.state == STATE_ON
    assert quarto.attributes[ATTR_INSTALLED_VERSION] == "1.2.0"
    assert quarto.attributes[ATTR_LATEST_VERSION] == "2.1.3.0"

    registry = er.async_get(hass)
    assert (
        registry.async_get("update.persiana_sala_firmware").unique_id == "101_firmware"
    )


async def test_update_falls_back_to_flag(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    cloud: CloudMock,
    setup_entry: MockConfigEntry,
) -> None:
    item = cloud.device("101")
    del item["lastestFirmwareVersion"]
    item["newFirmwareAvailable"] = True
    freezer.tick(timedelta(seconds=DEFAULT_SCAN_INTERVAL + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get("update.persiana_sala_firmware")
    assert state.attributes[ATTR_LATEST_VERSION] is None

    item["newFirmwareAvailable"] = False
    freezer.tick(timedelta(seconds=DEFAULT_SCAN_INTERVAL + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("update.persiana_sala_firmware").state == STATE_OFF
