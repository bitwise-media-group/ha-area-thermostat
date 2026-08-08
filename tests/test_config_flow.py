# Copyright 2026 BitWise Media Group Ltd
# SPDX-License-Identifier: MIT

"""Config flow, reconfigure, and options flow tests."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.area_thermostat.const import (
    CONF_ACT_DELTA,
    CONF_COOL_CAN_HEAT,
    CONF_COOL_ENTITY,
    CONF_HEAT_ACTIVE_PRESET,
    CONF_HEAT_ENTITY,
    CONF_HEAT_STRATEGY,
    CONF_IDLE_DELTA,
    CONF_TEMP_SENSOR,
    DEFAULT_OPTIONS,
    DOMAIN,
    STRATEGY_PRESET,
)

from .conftest import AIRCON, SENSOR, UFH, make_entry

USER_INPUT = {
    "name": "Guest Bedroom",
    CONF_TEMP_SENSOR: SENSOR,
    CONF_COOL_ENTITY: AIRCON,
    CONF_COOL_CAN_HEAT: True,
    CONF_HEAT_ENTITY: UFH,
}


async def test_full_flow_with_heat(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "heat_strategy"

    with patch(
        "custom_components.area_thermostat.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HEAT_STRATEGY: STRATEGY_PRESET,
                CONF_HEAT_ACTIVE_PRESET: "home",
                "heat_idle_preset": "standby",
            },
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Guest Bedroom"
    assert result["data"][CONF_HEAT_ENTITY] == UFH
    assert result["data"][CONF_HEAT_ACTIVE_PRESET] == "home"
    assert "name" not in result["data"]
    assert result["options"] == DEFAULT_OPTIONS


async def test_flow_without_heat_skips_strategy_step(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(
        "custom_components.area_thermostat.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "name": "Deavon's Office",
                CONF_TEMP_SENSOR: SENSOR,
                CONF_COOL_ENTITY: AIRCON,
                CONF_COOL_CAN_HEAT: True,
            },
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_HEAT_ENTITY not in result["data"]


async def test_same_entity_rejected(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**USER_INPUT, CONF_HEAT_ENTITY: AIRCON},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "same_entity"}


async def test_duplicate_area_aborts(hass: HomeAssistant) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_swaps_sensor(hass: HomeAssistant) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.area_thermostat.async_setup_entry", return_value=True
    ):
        result = await entry.start_reconfigure_flow(hass)
        assert result["step_id"] == "reconfigure"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_TEMP_SENSOR: "sensor.other_temperature",
                CONF_COOL_ENTITY: AIRCON,
                CONF_COOL_CAN_HEAT: True,
                CONF_HEAT_ENTITY: UFH,
                CONF_HEAT_STRATEGY: STRATEGY_PRESET,
                CONF_HEAT_ACTIVE_PRESET: "home",
                "heat_idle_preset": "standby",
            },
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_TEMP_SENSOR] == "sensor.other_temperature"


async def test_options_flow_validation_and_save(hass: HomeAssistant) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.area_thermostat.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["step_id"] == "init"

        bad = {**DEFAULT_OPTIONS, CONF_IDLE_DELTA: 2.0, CONF_ACT_DELTA: 1.5}
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], bad
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "idle_not_below_act"}

        good = {**DEFAULT_OPTIONS, CONF_ACT_DELTA: 2.0}
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], good
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_ACT_DELTA] == 2.0
