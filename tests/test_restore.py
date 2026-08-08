# Copyright 2026 BitWise Media Group Ltd
# SPDX-License-Identifier: MIT

"""Restart behavior: mode, setpoints, and the sticky latches survive."""

from __future__ import annotations

from homeassistant.components.climate import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    async_mock_service,
    mock_restore_cache,
)

from .conftest import AIRCON, setup_area

PROXY = "climate.guest_bedroom"


async def test_restore_heat_cool_with_range(hass: HomeAssistant) -> None:
    mock_restore_cache(
        hass,
        [
            State(
                PROXY,
                HVACMode.HEAT_COOL,
                {
                    ATTR_TARGET_TEMP_LOW: 18.0,
                    ATTR_TARGET_TEMP_HIGH: 24.0,
                    "heat_call": False,
                    "cool_call": False,
                    "aux_heat_call": False,
                    "last_non_off_mode": "heat_cool",
                },
            )
        ],
    )
    await setup_area(hass)
    state = hass.states.get(PROXY)
    assert state.state == HVACMode.HEAT_COOL
    assert state.attributes[ATTR_TARGET_TEMP_LOW] == 18.0
    assert state.attributes[ATTR_TARGET_TEMP_HIGH] == 24.0


async def test_restore_single_target_without_range_attrs(hass: HomeAssistant) -> None:
    mock_restore_cache(
        hass,
        [
            State(
                PROXY,
                HVACMode.HEAT,
                {ATTR_TEMPERATURE: 22.0, "last_non_off_mode": "heat"},
            )
        ],
    )
    await setup_area(hass)
    state = hass.states.get(PROXY)
    assert state.state == HVACMode.HEAT
    assert state.attributes[ATTR_TEMPERATURE] == 22.0


async def test_restored_aux_latch_keeps_boost_running(hass: HomeAssistant) -> None:
    """Restart mid-boost in the recovery band: the aircon must stay in heat.

    17.5 is above the boost engage point (16.5) but the restored aux latch is
    sticky, so a fresh evaluation must keep the aircon heating rather than
    switching it off.
    """
    set_temperature_calls = async_mock_service(hass, "climate", "set_temperature")
    set_hvac_calls = async_mock_service(hass, "climate", "set_hvac_mode")
    mock_restore_cache(
        hass,
        [
            State(
                PROXY,
                HVACMode.HEAT_COOL,
                {
                    ATTR_TARGET_TEMP_LOW: 19.5,
                    ATTR_TARGET_TEMP_HIGH: 22.5,
                    "heat_call": True,
                    "cool_call": False,
                    "aux_heat_call": True,
                    "last_non_off_mode": "heat_cool",
                },
            )
        ],
    )
    # NB: the climate component's own services replace these mocks during
    # setup, so assert on the proxy's state, not captured calls.
    del set_temperature_calls, set_hvac_calls
    await setup_area(hass, temperature=17.5)

    state = hass.states.get(PROXY)
    assert state.attributes["aux_heat_call"] is True
    assert state.attributes["hvac_action"] == "heating"
    # The initial evaluation asked the (state-faked) aircon to heat: the
    # controller's decision carries the heat-direction intent for it.
    entry = hass.config_entries.async_entries("area_thermostat")[0]
    decision = entry.runtime_data.last_decision
    aircon_intent = next(
        intent
        for intent in decision.intents
        if entry.runtime_data.cool_executor.source.entity_id == AIRCON
        and intent.role.value == "cool"
    )
    assert aircon_intent.active is True
    assert aircon_intent.direction.value == "heat"


async def test_restore_off_stays_off(hass: HomeAssistant) -> None:
    mock_restore_cache(hass, [State(PROXY, HVACMode.OFF, {})])
    await setup_area(hass, temperature=10.0)
    state = hass.states.get(PROXY)
    assert state.state == HVACMode.OFF
    assert state.attributes["hvac_action"] == "off"
