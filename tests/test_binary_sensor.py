"""Tests for the Udiconnect Plus binary sensor platform."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.udiconnect_plus.const import DEFAULT_SCAN_INTERVAL

from .conftest import CloudMock


async def test_sensor_states(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    assert hass.states.get("binary_sensor.persiana_sala_connectivity").state == STATE_ON
    assert hass.states.get("binary_sensor.persiana_sala_low_battery").state == STATE_OFF

    quarto_conn = hass.states.get("binary_sensor.persiana_quarto_connectivity")
    assert quarto_conn.state == STATE_OFF
    assert quarto_conn.attributes["device_class"] == "connectivity"
    assert (
        hass.states.get("binary_sensor.persiana_quarto_low_battery").state == STATE_ON
    )


async def test_sensor_registry_metadata(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    registry = er.async_get(hass)
    conn = registry.async_get("binary_sensor.persiana_sala_connectivity")
    assert conn.unique_id == "101_connectivity"
    assert conn.entity_category is EntityCategory.DIAGNOSTIC


async def test_sensors_follow_cloud(
    hass: HomeAssistant, cloud: CloudMock, setup_entry: MockConfigEntry
) -> None:
    cloud.device("101")["lowBattery"] = True
    cloud.sync_status = 200
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=DEFAULT_SCAN_INTERVAL + 1)
    )
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.persiana_sala_low_battery").state == STATE_ON

    cloud.sync_status = 500
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=DEFAULT_SCAN_INTERVAL + 1)
    )
    await hass.async_block_till_done()
    assert (
        hass.states.get("binary_sensor.persiana_sala_low_battery").state
        == STATE_UNAVAILABLE
    )
