# Copyright 2026 BitWise Media Group Ltd
# SPDX-License-Identifier: MIT

"""Config and options flows for Area Thermostat."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
)

from .const import (
    CONF_ACT_DELTA,
    CONF_BOOST_DELTA,
    CONF_COOL_CAN_HEAT,
    CONF_COOL_ENTITY,
    CONF_HEAT_ACTIVE_PRESET,
    CONF_HEAT_ENTITY,
    CONF_HEAT_IDLE_PRESET,
    CONF_HEAT_STRATEGY,
    CONF_IDLE_DELTA,
    CONF_KEEP_ALIVE,
    CONF_MAX_TEMP,
    CONF_MIN_COMMAND_INTERVAL,
    CONF_MIN_TEMP,
    CONF_SENSOR_STALE_TIMEOUT,
    CONF_TEMP_SENSOR,
    CONF_TEMP_STEP,
    DEFAULT_ACTIVE_PRESET,
    DEFAULT_IDLE_DELTA,
    DEFAULT_IDLE_PRESET,
    DEFAULT_OPTIONS,
    DOMAIN,
    MIN_RANGE_GAP,
    STRATEGY_HVAC_MODE,
    STRATEGY_PRESET,
)

_ENTITIES_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): TextSelector(),
        vol.Required(CONF_TEMP_SENSOR): EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class="temperature")
        ),
        vol.Required(CONF_COOL_ENTITY): EntitySelector(
            EntitySelectorConfig(domain="climate")
        ),
        vol.Required(CONF_COOL_CAN_HEAT, default=True): BooleanSelector(),
        vol.Optional(CONF_HEAT_ENTITY): EntitySelector(
            EntitySelectorConfig(domain="climate")
        ),
    }
)

_STRATEGY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HEAT_STRATEGY, default=STRATEGY_PRESET): SelectSelector(
            SelectSelectorConfig(
                options=[STRATEGY_PRESET, STRATEGY_HVAC_MODE],
                translation_key="heat_strategy",
            )
        ),
        vol.Required(
            CONF_HEAT_ACTIVE_PRESET, default=DEFAULT_ACTIVE_PRESET
        ): TextSelector(),
        vol.Required(
            CONF_HEAT_IDLE_PRESET, default=DEFAULT_IDLE_PRESET
        ): TextSelector(),
    }
)


def _options_schema(options: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {}
    # required=False fields may be left empty; their key is then absent from
    # the saved options (CONF_IDLE_DELTA: empty means auto release).
    for key, minimum, maximum, step, unit, required in (
        (CONF_ACT_DELTA, 0.1, 5.0, 0.1, "°C", True),
        (CONF_IDLE_DELTA, 0.1, 5.0, 0.1, "°C", False),
        (CONF_BOOST_DELTA, 0.5, 10.0, 0.1, "°C", True),
        (CONF_MIN_TEMP, 5.0, 25.0, 0.5, "°C", True),
        (CONF_MAX_TEMP, 15.0, 35.0, 0.5, "°C", True),
        (CONF_TEMP_STEP, 0.1, 1.0, 0.1, "°C", True),
        (CONF_KEEP_ALIVE, 0, 3600, 30, "s", True),
        (CONF_MIN_COMMAND_INTERVAL, 0, 120, 1, "s", True),
        (CONF_SENSOR_STALE_TIMEOUT, 0, 7200, 60, "s", True),
    ):
        selector = NumberSelector(
            NumberSelectorConfig(
                min=minimum,
                max=maximum,
                step=step,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement=unit,
            )
        )
        marker = (
            vol.Required(key, default=options[key])
            if required
            else vol.Optional(key, description={"suggested_value": options.get(key)})
        )
        fields[marker] = selector
    return vol.Schema(fields)


class AreaThermostatConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create one area thermostat per config entry."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> AreaThermostatOptionsFlow:
        return AreaThermostatOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the area's entities."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_COOL_ENTITY] == user_input.get(CONF_HEAT_ENTITY):
                errors["base"] = "same_entity"
            else:
                self._async_abort_entries_match(
                    {
                        CONF_TEMP_SENSOR: user_input[CONF_TEMP_SENSOR],
                        CONF_COOL_ENTITY: user_input[CONF_COOL_ENTITY],
                    }
                )
                self._data = dict(user_input)
                if self._data.get(CONF_HEAT_ENTITY):
                    return await self.async_step_heat_strategy()
                return self._async_create()
        return self.async_show_form(
            step_id="user", data_schema=_ENTITIES_SCHEMA, errors=errors
        )

    async def async_step_heat_strategy(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Describe how the primary heat source is commanded."""
        if user_input is not None:
            self._data.update(user_input)
            return self._async_create()
        return self.async_show_form(
            step_id="heat_strategy", data_schema=_STRATEGY_SCHEMA
        )

    def _async_create(self) -> ConfigFlowResult:
        title = self._data.pop(CONF_NAME)
        return self.async_create_entry(
            title=title, data=self._data, options=dict(DEFAULT_OPTIONS)
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Swap the area's entities without recreating the entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_COOL_ENTITY] == user_input.get(CONF_HEAT_ENTITY):
                errors["base"] = "same_entity"
            else:
                data = dict(user_input)
                if not data.get(CONF_HEAT_ENTITY):
                    for key in (
                        CONF_HEAT_ENTITY,
                        CONF_HEAT_STRATEGY,
                        CONF_HEAT_ACTIVE_PRESET,
                        CONF_HEAT_IDLE_PRESET,
                    ):
                        data.pop(key, None)
                return self.async_update_reload_and_abort(entry, data=data)

        current = entry.data
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_TEMP_SENSOR, default=current.get(CONF_TEMP_SENSOR)
                ): EntitySelector(
                    EntitySelectorConfig(domain="sensor", device_class="temperature")
                ),
                vol.Required(
                    CONF_COOL_ENTITY, default=current.get(CONF_COOL_ENTITY)
                ): EntitySelector(EntitySelectorConfig(domain="climate")),
                vol.Required(
                    CONF_COOL_CAN_HEAT, default=current.get(CONF_COOL_CAN_HEAT, True)
                ): BooleanSelector(),
                vol.Optional(
                    CONF_HEAT_ENTITY,
                    description={"suggested_value": current.get(CONF_HEAT_ENTITY)},
                ): EntitySelector(EntitySelectorConfig(domain="climate")),
                vol.Required(
                    CONF_HEAT_STRATEGY,
                    default=current.get(CONF_HEAT_STRATEGY, STRATEGY_PRESET),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[STRATEGY_PRESET, STRATEGY_HVAC_MODE],
                        translation_key="heat_strategy",
                    )
                ),
                vol.Required(
                    CONF_HEAT_ACTIVE_PRESET,
                    default=current.get(CONF_HEAT_ACTIVE_PRESET, DEFAULT_ACTIVE_PRESET),
                ): TextSelector(),
                vol.Required(
                    CONF_HEAT_IDLE_PRESET,
                    default=current.get(CONF_HEAT_IDLE_PRESET, DEFAULT_IDLE_PRESET),
                ): TextSelector(),
            }
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )


class AreaThermostatOptionsFlow(OptionsFlow):
    """Tune the bands, timings, and temperature limits."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            # An empty release hysteresis means auto: midpoint release in
            # heat_cool, DEFAULT_IDLE_DELTA in single-target modes — the
            # latter is what the act threshold must stay above.
            idle = user_input.get(CONF_IDLE_DELTA, DEFAULT_IDLE_DELTA)
            if idle >= user_input[CONF_ACT_DELTA]:
                errors["base"] = "idle_not_below_act"
            elif user_input[CONF_MIN_TEMP] >= user_input[CONF_MAX_TEMP] - MIN_RANGE_GAP:
                errors["base"] = "min_not_below_max"
            else:
                return self.async_create_entry(data=user_input)
        options = {**DEFAULT_OPTIONS, **self.config_entry.options}
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(options), errors=errors
        )
