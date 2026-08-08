# Copyright 2026 BitWise Media Group Ltd
# SPDX-License-Identifier: MIT

"""Shared fixtures: a mock area with a sensor, an aircon, and a UFH stat."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.area_thermostat.const import (
    CONF_COOL_CAN_HEAT,
    CONF_COOL_ENTITY,
    CONF_HEAT_ACTIVE_PRESET,
    CONF_HEAT_ENTITY,
    CONF_HEAT_IDLE_PRESET,
    CONF_HEAT_STRATEGY,
    CONF_KEEP_ALIVE,
    CONF_MIN_COMMAND_INTERVAL,
    CONF_TEMP_SENSOR,
    DEFAULT_OPTIONS,
    DOMAIN,
    STRATEGY_PRESET,
)

SENSOR = "sensor.mock_temperature"
AIRCON = "climate.mock_aircon"
UFH = "climate.mock_ufh"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ for every test."""
    return


def set_sensor(hass: HomeAssistant, value: float | str) -> None:
    hass.states.async_set(SENSOR, str(value), {"device_class": "temperature"})


def set_aircon(
    hass: HomeAssistant, hvac_mode: str = "off", temperature: float | None = None
) -> None:
    hass.states.async_set(AIRCON, hvac_mode, {"temperature": temperature})


def set_ufh(
    hass: HomeAssistant,
    state: str = "auto",
    preset_mode: str | None = "standby",
    temperature: float | None = None,
) -> None:
    hass.states.async_set(
        UFH, state, {"preset_mode": preset_mode, "temperature": temperature}
    )


def make_entry(
    *,
    with_heat: bool = True,
    cool_can_heat: bool = True,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    data: dict[str, Any] = {
        CONF_TEMP_SENSOR: SENSOR,
        CONF_COOL_ENTITY: AIRCON,
        CONF_COOL_CAN_HEAT: cool_can_heat,
    }
    if with_heat:
        data |= {
            CONF_HEAT_ENTITY: UFH,
            CONF_HEAT_STRATEGY: STRATEGY_PRESET,
            CONF_HEAT_ACTIVE_PRESET: "home",
            CONF_HEAT_IDLE_PRESET: "standby",
        }
    # Throttle and keep-alive off by default so tests are deterministic;
    # the dispatch tests opt back in explicitly.
    merged_options = {
        **DEFAULT_OPTIONS,
        CONF_MIN_COMMAND_INTERVAL: 0,
        CONF_KEEP_ALIVE: 0,
        **(options or {}),
    }
    return MockConfigEntry(
        domain=DOMAIN,
        title="Guest Bedroom",
        data=data,
        options=merged_options,
    )


async def setup_area(
    hass: HomeAssistant,
    *,
    with_heat: bool = True,
    cool_can_heat: bool = True,
    options: dict[str, Any] | None = None,
    temperature: float | str = 21.0,
) -> MockConfigEntry:
    """Stand the whole area up with in-sync (idle) source states."""
    set_sensor(hass, temperature)
    set_aircon(hass)
    if with_heat:
        set_ufh(hass)
    entry = make_entry(
        with_heat=with_heat, cool_can_heat=cool_can_heat, options=options
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def advance(hass: HomeAssistant, seconds: float) -> None:
    """Fire timers up to now+seconds (debounce, throttle flush, keep-alive).

    Drains the loop first: state-change listeners arm their timers via loop
    callbacks, so firing the clock immediately after ``async_set`` would race
    past a not-yet-armed debounce.
    """
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


def proxy_entity(hass: HomeAssistant, entity_id: str = "climate.guest_bedroom"):
    """The live entity object — driven directly because the tests replace the
    climate services with mocks to capture what the executors send."""
    return hass.data["entity_components"]["climate"].get_entity(entity_id)
