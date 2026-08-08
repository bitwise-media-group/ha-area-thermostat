# Copyright 2026 BitWise Media Group Ltd
# SPDX-License-Identifier: MIT

"""SourceExecutor tests: dedupe, throttle coalescing, keep-alive force."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.area_thermostat.engine import Direction, Role, SourceIntent
from custom_components.area_thermostat.sources import SourceConfig, SourceExecutor

from .conftest import advance

ENTITY = "climate.fake_unit"


@pytest.fixture
def calls(hass: HomeAssistant):
    return {
        "set_hvac_mode": async_mock_service(hass, "climate", "set_hvac_mode"),
        "set_preset_mode": async_mock_service(hass, "climate", "set_preset_mode"),
        "set_temperature": async_mock_service(hass, "climate", "set_temperature"),
    }


def hvac_executor(hass: HomeAssistant, min_interval: float = 0) -> SourceExecutor:
    return SourceExecutor(
        hass,
        SourceConfig(entity_id=ENTITY, roles=frozenset({Role.COOL})),
        min_interval,
    )


def preset_executor(hass: HomeAssistant) -> SourceExecutor:
    return SourceExecutor(
        hass,
        SourceConfig(
            entity_id=ENTITY,
            roles=frozenset({Role.HEAT_PRIMARY}),
            strategy="preset",
            active_preset="home",
            idle_preset="standby",
        ),
        0,
    )


def intent(
    active: bool, direction: Direction | None = None, setpoint: float | None = None
) -> SourceIntent:
    return SourceIntent(
        role=Role.COOL, active=active, direction=direction, setpoint=setpoint
    )


async def test_dedupe_skips_matching_state(hass: HomeAssistant, calls) -> None:
    hass.states.async_set(ENTITY, "cool", {"temperature": 23.0})
    executor = hvac_executor(hass)
    await executor.async_apply(intent(True, Direction.COOL, 23.0))
    await hass.async_block_till_done()
    assert not calls["set_hvac_mode"]
    assert not calls["set_temperature"]


async def test_mode_and_setpoint_sent_when_different(
    hass: HomeAssistant, calls
) -> None:
    hass.states.async_set(ENTITY, "off", {"temperature": 20.0})
    executor = hvac_executor(hass)
    await executor.async_apply(intent(True, Direction.HEAT, 22.0))
    await hass.async_block_till_done()
    assert calls["set_hvac_mode"][0].data["hvac_mode"] == "heat"
    assert calls["set_temperature"][0].data["temperature"] == 22.0


async def test_force_resends_matching_state(hass: HomeAssistant, calls) -> None:
    hass.states.async_set(ENTITY, "cool", {"temperature": 23.0})
    executor = hvac_executor(hass)
    await executor.async_apply(intent(True, Direction.COOL, 23.0), force=True)
    await hass.async_block_till_done()
    assert len(calls["set_hvac_mode"]) == 1
    assert len(calls["set_temperature"]) == 1


async def test_unavailable_entity_is_skipped(hass: HomeAssistant, calls) -> None:
    hass.states.async_set(ENTITY, "unavailable")
    executor = hvac_executor(hass)
    await executor.async_apply(intent(True, Direction.COOL, 23.0))
    await hass.async_block_till_done()
    assert not calls["set_hvac_mode"]


async def test_throttle_coalesces_to_newest_intent(hass: HomeAssistant, calls) -> None:
    hass.states.async_set(ENTITY, "off", {"temperature": None})
    executor = hvac_executor(hass, min_interval=10)

    await executor.async_apply(intent(True, Direction.COOL, 23.0))
    await hass.async_block_till_done()
    assert len(calls["set_hvac_mode"]) == 1

    # Two rapid follow-ups inside the window: only the newest survives, and
    # it is sent when the flush timer fires.
    await executor.async_apply(intent(False, None, 21.0))
    await executor.async_apply(intent(True, Direction.HEAT, 19.0))
    await hass.async_block_till_done()
    assert len(calls["set_hvac_mode"]) == 1  # still throttled

    await advance(hass, 11)
    assert len(calls["set_hvac_mode"]) == 2
    assert calls["set_hvac_mode"][-1].data["hvac_mode"] == "heat"
    assert calls["set_temperature"][-1].data["temperature"] == 19.0


async def test_preset_strategy_call_shapes(hass: HomeAssistant, calls) -> None:
    hass.states.async_set(ENTITY, "auto", {"preset_mode": "standby"})
    executor = preset_executor(hass)

    await executor.async_apply(
        SourceIntent(
            role=Role.HEAT_PRIMARY, active=True, direction=Direction.HEAT, setpoint=19.0
        )
    )
    await hass.async_block_till_done()
    assert calls["set_preset_mode"][0].data["preset_mode"] == "home"
    assert not calls["set_hvac_mode"]  # preset strategy never touches hvac_mode

    hass.states.async_set(ENTITY, "auto", {"preset_mode": "home", "temperature": 19.0})
    await executor.async_apply(
        SourceIntent(
            role=Role.HEAT_PRIMARY, active=False, direction=None, setpoint=19.0
        )
    )
    await hass.async_block_till_done()
    assert calls["set_preset_mode"][-1].data["preset_mode"] == "standby"
    assert len(calls["set_temperature"]) == 1  # 19.0 already in sync second time
