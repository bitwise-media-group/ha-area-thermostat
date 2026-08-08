# Copyright 2026 BitWise Media Group Ltd
# SPDX-License-Identifier: MIT

"""Entity-level tests: sensor changes drive service calls on the sources."""

from __future__ import annotations

import pytest
from homeassistant.components.climate import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_mock_service

from .conftest import (
    AIRCON,
    UFH,
    advance,
    proxy_entity,
    set_aircon,
    set_sensor,
    set_ufh,
    setup_area,
)

PROXY = "climate.guest_bedroom"


@pytest.fixture
def climate_calls(hass: HomeAssistant):
    """Capture climate service calls made by the executors.

    Registered by the tests *after* setup (the real climate integration
    re-registers its services during setup, which would clobber earlier
    mocks), so these fixtures return factories.
    """

    def _register() -> dict[str, list]:
        return {
            "set_hvac_mode": async_mock_service(hass, "climate", "set_hvac_mode"),
            "set_preset_mode": async_mock_service(hass, "climate", "set_preset_mode"),
            "set_temperature": async_mock_service(hass, "climate", "set_temperature"),
        }

    return _register


def calls_for(calls: list, entity_id: str) -> list:
    return [call for call in calls if call.data["entity_id"] == entity_id]


async def test_setup_creates_entity_with_full_mode_set(hass: HomeAssistant) -> None:
    await setup_area(hass)
    state = hass.states.get(PROXY)
    assert state is not None
    assert state.state == HVACMode.OFF
    assert state.attributes["hvac_modes"] == [
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.COOL,
        HVACMode.HEAT_COOL,
    ]
    assert state.attributes["current_temperature"] == 21.0


async def test_cooling_only_area_has_no_heat_modes(hass: HomeAssistant) -> None:
    await setup_area(hass, with_heat=False, cool_can_heat=False)
    state = hass.states.get(PROXY)
    assert state.attributes["hvac_modes"] == [HVACMode.OFF, HVACMode.COOL]


async def test_supported_features_flip_with_mode(hass: HomeAssistant) -> None:
    await setup_area(hass)
    entity = proxy_entity(hass, PROXY)

    await entity.async_set_hvac_mode(HVACMode.HEAT_COOL)
    await hass.async_block_till_done()
    state = hass.states.get(PROXY)
    features = state.attributes["supported_features"]
    assert features & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
    assert not features & ClimateEntityFeature.TARGET_TEMPERATURE
    assert ATTR_TARGET_TEMP_LOW in state.attributes
    assert state.attributes.get(ATTR_TEMPERATURE) is None

    await entity.async_set_hvac_mode(HVACMode.HEAT)
    await hass.async_block_till_done()
    state = hass.states.get(PROXY)
    features = state.attributes["supported_features"]
    assert features & ClimateEntityFeature.TARGET_TEMPERATURE
    assert not features & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
    assert state.attributes.get(ATTR_TEMPERATURE) is not None


async def test_cold_room_calls_ufh_then_boosts_aircon(
    hass: HomeAssistant, climate_calls
) -> None:
    await setup_area(hass)
    calls = climate_calls()
    entity = proxy_entity(hass, PROXY)
    await entity.async_set_hvac_mode(HVACMode.HEAT_COOL)
    await hass.async_block_till_done()

    # Mildly cold (below the default low of 19.5): UFH leads, no aircon heat.
    set_sensor(hass, 18.5)
    await advance(hass, 1.1)
    ufh_presets = calls_for(calls["set_preset_mode"], UFH)
    assert ufh_presets and ufh_presets[-1].data["preset_mode"] == "home"
    assert not calls_for(calls["set_hvac_mode"], AIRCON) or all(
        call.data["hvac_mode"] == "off"
        for call in calls_for(calls["set_hvac_mode"], AIRCON)
    )
    assert hass.states.get(PROXY).attributes["hvac_action"] == "heating"

    # Very cold (more than boost_delta below the low threshold): aircon boosts.
    set_sensor(hass, 15.0)
    await advance(hass, 1.1)
    aircon_modes = calls_for(calls["set_hvac_mode"], AIRCON)
    assert aircon_modes and aircon_modes[-1].data["hvac_mode"] == "heat"


async def test_hot_room_cools_and_stands_ufh_down(
    hass: HomeAssistant, climate_calls
) -> None:
    await setup_area(hass)
    calls = climate_calls()
    entity = proxy_entity(hass, PROXY)
    await entity.async_set_hvac_mode(HVACMode.HEAT_COOL)
    await hass.async_block_till_done()

    set_ufh(hass, preset_mode="home")  # pretend it was left heating
    set_sensor(hass, 25.0)
    await advance(hass, 1.1)

    aircon_modes = calls_for(calls["set_hvac_mode"], AIRCON)
    assert aircon_modes and aircon_modes[-1].data["hvac_mode"] == "cool"
    ufh_presets = calls_for(calls["set_preset_mode"], UFH)
    assert ufh_presets and ufh_presets[-1].data["preset_mode"] == "standby"
    assert hass.states.get(PROXY).attributes["hvac_action"] == "cooling"


async def test_heat_mode_never_cools(hass: HomeAssistant, climate_calls) -> None:
    await setup_area(hass)
    calls = climate_calls()
    entity = proxy_entity(hass, PROXY)
    await entity.async_set_hvac_mode(HVACMode.HEAT)
    await hass.async_block_till_done()

    set_sensor(hass, 30.0)
    await advance(hass, 1.1)
    assert all(
        call.data["hvac_mode"] != "cool"
        for call in calls_for(calls["set_hvac_mode"], AIRCON)
    )


async def test_ufh_offline_falls_back_to_aircon_heat(
    hass: HomeAssistant, climate_calls
) -> None:
    await setup_area(hass)
    calls = climate_calls()
    entity = proxy_entity(hass, PROXY)
    await entity.async_set_hvac_mode(HVACMode.HEAT)
    await hass.async_block_till_done()

    hass.states.async_set(UFH, "unavailable")
    set_sensor(hass, 18.0)  # mildly cold — would normally be UFH-only
    await advance(hass, 1.1)

    aircon_modes = calls_for(calls["set_hvac_mode"], AIRCON)
    assert aircon_modes and aircon_modes[-1].data["hvac_mode"] == "heat"
    # Nothing was sent to the unavailable stat.
    assert not calls_for(calls["set_preset_mode"], UFH)


async def test_turn_off_idles_everything(hass: HomeAssistant, climate_calls) -> None:
    await setup_area(hass)
    calls = climate_calls()
    entity = proxy_entity(hass, PROXY)
    await entity.async_set_hvac_mode(HVACMode.HEAT_COOL)
    await hass.async_block_till_done()
    set_sensor(hass, 25.0)
    await advance(hass, 1.1)
    set_aircon(hass, "cool")  # device followed the cool command

    await entity.async_turn_off()
    await hass.async_block_till_done()

    assert hass.states.get(PROXY).state == HVACMode.OFF
    aircon_modes = calls_for(calls["set_hvac_mode"], AIRCON)
    assert aircon_modes[-1].data["hvac_mode"] == "off"

    # turn_on restores the last non-off mode.
    await entity.async_turn_on()
    await hass.async_block_till_done()
    assert hass.states.get(PROXY).state == HVACMode.HEAT_COOL


async def test_set_temperature_range_enforces_gap(hass: HomeAssistant) -> None:
    await setup_area(hass)
    entity = proxy_entity(hass, PROXY)
    await entity.async_set_hvac_mode(HVACMode.HEAT_COOL)
    await hass.async_block_till_done()

    await entity.async_set_temperature(
        **{ATTR_TARGET_TEMP_LOW: 22.0, ATTR_TARGET_TEMP_HIGH: 22.3}
    )
    await hass.async_block_till_done()
    state = hass.states.get(PROXY)
    low = state.attributes[ATTR_TARGET_TEMP_LOW]
    high = state.attributes[ATTR_TARGET_TEMP_HIGH]
    assert high - low >= 1.0


async def test_single_target_clamped_to_limits(hass: HomeAssistant) -> None:
    await setup_area(hass)
    entity = proxy_entity(hass, PROXY)
    await entity.async_set_hvac_mode(HVACMode.HEAT)
    await hass.async_block_till_done()

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 50.0})
    await hass.async_block_till_done()
    assert hass.states.get(PROXY).attributes[ATTR_TEMPERATURE] == 30.0


async def test_sensor_unavailable_holds_then_fails_safe(
    hass: HomeAssistant, climate_calls
) -> None:
    await setup_area(hass, options={"sensor_stale_timeout": 900})
    calls = climate_calls()
    entity = proxy_entity(hass, PROXY)
    await entity.async_set_hvac_mode(HVACMode.HEAT_COOL)
    await hass.async_block_till_done()
    set_sensor(hass, 15.0)
    await advance(hass, 1.1)
    set_aircon(hass, "heat")  # the boost took

    hass.states.async_set("sensor.mock_temperature", "unavailable")
    await advance(hass, 1.1)
    # Held: no off command yet, current_temperature is unknown.
    state = hass.states.get(PROXY)
    assert state.attributes["current_temperature"] is None
    assert all(
        call.data["hvac_mode"] != "off"
        for call in calls_for(calls["set_hvac_mode"], AIRCON)
    )

    # After the stale timeout everything idles.
    await advance(hass, 901)
    aircon_modes = calls_for(calls["set_hvac_mode"], AIRCON)
    assert aircon_modes and aircon_modes[-1].data["hvac_mode"] == "off"
    ufh_presets = calls_for(calls["set_preset_mode"], UFH)
    assert ufh_presets and ufh_presets[-1].data["preset_mode"] == "standby"
