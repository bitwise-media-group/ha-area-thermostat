# Copyright 2026 BitWise Media Group Ltd
# SPDX-License-Identifier: MIT

"""The area thermostat climate entity — the HomeKit/Control4 surface.

Presents the controller's desired state as a single thermostat: single-target
in HEAT/COOL, a low/high range in HEAT_COOL (which HomeKit's Heater Cooler
accessory maps onto its heating/cooling thresholds), and a live
``hvac_action`` from the engine's arbitration.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, MIN_RANGE_GAP
from .controller import AreaThermostatConfigEntry, AreaThermostatController
from .engine import Mode

ATTR_LAST_NON_OFF_MODE = "last_non_off_mode"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AreaThermostatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the climate entity for a config entry."""
    async_add_entities([AreaThermostatEntity(entry.runtime_data, entry)])


class AreaThermostatEntity(ClimateEntity, RestoreEntity):
    """One area's virtual thermostat over its real heating/cooling sources."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(
        self, controller: AreaThermostatController, entry: AreaThermostatConfigEntry
    ) -> None:
        self._controller = controller
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            entry_type=DeviceEntryType.SERVICE,
        )
        modes = [HVACMode.OFF, HVACMode.COOL]
        if controller.has_heat_source:
            modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL]
        self._attr_hvac_modes = modes
        self._attr_min_temp = controller.min_temp
        self._attr_max_temp = controller.max_temp
        self._attr_target_temperature_step = controller.temp_step

    # ---- state presentation --------------------------------------------

    @property
    def supported_features(self) -> ClimateEntityFeature:
        features = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        if self._controller.mode is Mode.HEAT_COOL:
            return features | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        return features | ClimateEntityFeature.TARGET_TEMPERATURE

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode(self._controller.mode.value)

    @property
    def hvac_action(self) -> HVACAction:
        if self._controller.mode is Mode.OFF:
            return HVACAction.OFF
        decision = self._controller.last_decision
        if decision is None:
            return HVACAction.IDLE
        return HVACAction(decision.action.value)

    @property
    def current_temperature(self) -> float | None:
        return self._controller.current_temperature()

    @property
    def target_temperature(self) -> float | None:
        if self._controller.mode is Mode.HEAT_COOL:
            return None
        return self._controller.target

    @property
    def target_temperature_low(self) -> float | None:
        if self._controller.mode is Mode.HEAT_COOL:
            return self._controller.target_low
        return None

    @property
    def target_temperature_high(self) -> float | None:
        if self._controller.mode is Mode.HEAT_COOL:
            return self._controller.target_high
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            **self._controller.engine.snapshot(),
            ATTR_LAST_NON_OFF_MODE: self._controller.last_non_off_mode.value,
        }

    # ---- commands ------------------------------------------------------

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._controller.set_mode(Mode(hvac_mode.value))
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(
            HVACMode(self._controller.last_non_off_mode.value)
        )

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (hvac_mode := kwargs.get("hvac_mode")) is not None:
            self._controller.set_mode(Mode(HVACMode(hvac_mode).value))

        low = kwargs.get(ATTR_TARGET_TEMP_LOW)
        high = kwargs.get(ATTR_TARGET_TEMP_HIGH)
        target = kwargs.get(ATTR_TEMPERATURE)

        if low is not None or high is not None:
            if low is None:
                low = self._controller.target_low
            if high is None:
                high = self._controller.target_high
            low = self._clamp(low, self.min_temp, self.max_temp - MIN_RANGE_GAP)
            high = self._clamp(high, low + MIN_RANGE_GAP, self.max_temp)
            self._controller.set_targets(target_low=low, target_high=high)
        elif target is not None:
            if self._controller.mode is Mode.HEAT_COOL:
                # A single value while in range mode (some bridges do this):
                # recentre the range around it, preserving its width.
                width = self._controller.target_high - self._controller.target_low
                low = self._clamp(
                    target - width / 2, self.min_temp, self.max_temp - width
                )
                self._controller.set_targets(target_low=low, target_high=low + width)
            else:
                self._controller.set_targets(
                    target=self._clamp(target, self.min_temp, self.max_temp)
                )
        self.async_write_ha_state()

    # ---- lifecycle -----------------------------------------------------

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._async_restore_state()
        self._controller.set_update_callback(self._on_controller_update)
        await self._controller.async_start()

    async def async_will_remove_from_hass(self) -> None:
        self._controller.set_update_callback(None)
        await self._controller.async_stop()
        await super().async_will_remove_from_hass()

    @callback
    def _on_controller_update(self) -> None:
        self.async_write_ha_state()

    async def _async_restore_state(self) -> None:
        last = await self.async_get_last_state()
        if last is None:
            return
        try:
            mode = Mode(last.state)
        except ValueError:
            mode = Mode.OFF
        attrs = last.attributes
        try:
            last_non_off = Mode(attrs.get(ATTR_LAST_NON_OFF_MODE, ""))
        except ValueError:
            last_non_off = mode if mode is not Mode.OFF else Mode.HEAT_COOL
        self._controller.restore(
            mode=mode,
            last_non_off_mode=last_non_off,
            target=self._as_float(attrs.get(ATTR_TEMPERATURE)),
            target_low=self._as_float(attrs.get(ATTR_TARGET_TEMP_LOW)),
            target_high=self._as_float(attrs.get(ATTR_TARGET_TEMP_HIGH)),
            latches={
                "heat_call": bool(attrs.get("heat_call", False)),
                "cool_call": bool(attrs.get("cool_call", False)),
                "aux_heat_call": bool(attrs.get("aux_heat_call", False)),
            },
        )

    @staticmethod
    def _as_float(value: Any) -> float | None:
        return value if isinstance(value, int | float) else None

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return min(max(value, low), high)
