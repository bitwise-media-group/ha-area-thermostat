# Copyright 2026 BitWise Media Group Ltd
# SPDX-License-Identifier: MIT

"""Orchestration: wires the pure engine to Home Assistant.

Owns the desired state (mode + setpoints), subscribes to the temperature
sensor and the underlying source entities, and runs an engine evaluation

- after a 1 s coalescing debounce on state changes (bursts from a device
  round-trip echo settle before we react — and our own commands bouncing
  back never trigger an immediate re-evaluation loop),
- immediately on user actions (mode/setpoint changes),
- on a keep-alive interval as a *forced* pass that re-asserts hvac modes,
  presets, and setpoints (the blueprint's every-run mirroring: heals manual
  fiddling at the wall stat and device reboots),
- and as a fail-safe: when the sensor has been unusable for
  ``sensor_stale_timeout`` seconds, every source is forced idle rather than
  left running on stale data. Until that timeout the last commanded state is
  held — safe, because mirrored setpoints mean the devices self-limit.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
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
    DEFAULT_IDLE_PRESET,
    DEFAULT_OPTIONS,
    DEFAULT_TARGET,
    DEFAULT_TARGET_HIGH,
    DEFAULT_TARGET_LOW,
    EVALUATION_DEBOUNCE,
    STRATEGY_PRESET,
)
from .engine import (
    ClimateEngine,
    Decision,
    EngineConfig,
    EngineInputs,
    Mode,
    Role,
)
from .sources import SourceConfig, SourceExecutor

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)

type AreaThermostatConfigEntry = ConfigEntry[AreaThermostatController]


class AreaThermostatController:
    """One area: desired state, event wiring, and intent dispatch."""

    def __init__(self, hass: HomeAssistant, entry: AreaThermostatConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.options = {**DEFAULT_OPTIONS, **entry.options}

        data = entry.data
        self.sensor_entity_id: str = data[CONF_TEMP_SENSOR]
        min_interval = float(self.options[CONF_MIN_COMMAND_INTERVAL])

        cool_roles = {Role.COOL}
        if data.get(CONF_COOL_CAN_HEAT, True):
            cool_roles.add(Role.HEAT_BOOST)
        self.cool_executor = SourceExecutor(
            hass,
            SourceConfig(entity_id=data[CONF_COOL_ENTITY], roles=frozenset(cool_roles)),
            min_interval,
        )

        self.heat_executor: SourceExecutor | None = None
        if data.get(CONF_HEAT_ENTITY):
            self.heat_executor = SourceExecutor(
                hass,
                SourceConfig(
                    entity_id=data[CONF_HEAT_ENTITY],
                    roles=frozenset({Role.HEAT_PRIMARY}),
                    strategy=data.get(CONF_HEAT_STRATEGY, STRATEGY_PRESET),
                    active_preset=data.get(
                        CONF_HEAT_ACTIVE_PRESET, DEFAULT_ACTIVE_PRESET
                    ),
                    idle_preset=data.get(CONF_HEAT_IDLE_PRESET, DEFAULT_IDLE_PRESET),
                ),
                min_interval,
            )

        self.engine = ClimateEngine(
            EngineConfig(
                act_delta=float(self.options[CONF_ACT_DELTA]),
                idle_delta=float(self.options[CONF_IDLE_DELTA]),
                boost_delta=float(self.options[CONF_BOOST_DELTA]),
            )
        )

        # Desired state — owned here, presented by the climate entity.
        self.mode: Mode = Mode.OFF
        self.last_non_off_mode: Mode = Mode.HEAT_COOL
        self.target: float = self._clamp(DEFAULT_TARGET)
        self.target_low: float = self._clamp(DEFAULT_TARGET_LOW)
        self.target_high: float = self._clamp(DEFAULT_TARGET_HIGH)
        self.last_decision: Decision | None = None

        self._update_cb: Callable[[], None] | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._debounce_unsub: CALLBACK_TYPE | None = None
        self._stale_unsub: CALLBACK_TYPE | None = None

    # ---- lifecycle -----------------------------------------------------

    async def async_start(self) -> None:
        """Subscribe to events and run the first evaluation."""
        watched = [self.sensor_entity_id, self.cool_executor.source.entity_id]
        if self.heat_executor is not None:
            watched.append(self.heat_executor.source.entity_id)
        self._unsubs.append(
            async_track_state_change_event(self.hass, watched, self._on_state_event)
        )
        keep_alive = float(self.options[CONF_KEEP_ALIVE])
        if keep_alive > 0:
            self._unsubs.append(
                async_track_time_interval(
                    self.hass, self._on_keep_alive, timedelta(seconds=keep_alive)
                )
            )
        await self._async_evaluate()

    async def async_stop(self) -> None:
        """Tear down every subscription and timer (idempotent)."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._cancel_debounce()
        self._cancel_stale_timer()
        self.cool_executor.stop()
        if self.heat_executor is not None:
            self.heat_executor.stop()

    def set_update_callback(self, update_cb: Callable[[], None] | None) -> None:
        self._update_cb = update_cb

    # ---- desired state (called by the climate entity) ------------------

    @property
    def has_heat_source(self) -> bool:
        return self.heat_executor is not None or Role.HEAT_BOOST in (
            self.cool_executor.source.roles
        )

    def restore(
        self,
        *,
        mode: Mode,
        last_non_off_mode: Mode,
        target: float | None,
        target_low: float | None,
        target_high: float | None,
        latches: dict[str, bool],
    ) -> None:
        """Reinstate persisted state before the first evaluation."""
        self.mode = mode
        self.last_non_off_mode = last_non_off_mode
        if target is not None:
            self.target = self._clamp(target)
        if target_low is not None:
            self.target_low = self._clamp(target_low)
        if target_high is not None:
            self.target_high = self._clamp(target_high)
        self.engine.restore(**latches)

    def set_mode(self, mode: Mode) -> None:
        self.mode = mode
        if mode is not Mode.OFF:
            self.last_non_off_mode = mode
        self._schedule_evaluate()

    def set_targets(
        self,
        *,
        target: float | None = None,
        target_low: float | None = None,
        target_high: float | None = None,
    ) -> None:
        if target is not None:
            self.target = target
        if target_low is not None:
            self.target_low = target_low
        if target_high is not None:
            self.target_high = target_high
        self._schedule_evaluate()

    def current_temperature(self) -> float | None:
        state = self.hass.states.get(self.sensor_entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    # ---- evaluation ----------------------------------------------------

    def _schedule_evaluate(self) -> None:
        """Immediate (non-debounced) evaluation for user actions."""
        self._cancel_debounce()
        self.entry.async_create_task(
            self.hass, self._async_evaluate(), "area_thermostat_evaluate"
        )

    async def _async_evaluate(self, *, force: bool = False) -> None:
        inputs = self._build_inputs()
        decision = self.engine.evaluate(inputs)
        self.last_decision = decision
        self._manage_stale_timer(inputs.current_temp is None)
        if decision.valid:
            await self._async_dispatch(decision, force=force)
        self._notify()

    def _build_inputs(self) -> EngineInputs:
        heat = self.heat_executor
        return EngineInputs(
            mode=self.mode,
            current_temp=self.current_temperature(),
            target=self.target,
            target_low=self.target_low,
            target_high=self.target_high,
            has_heat_primary=heat is not None,
            heat_primary_available=heat.is_available() if heat is not None else False,
            has_heat_boost=Role.HEAT_BOOST in self.cool_executor.source.roles,
        )

    async def _async_dispatch(self, decision: Decision, *, force: bool) -> None:
        for intent in decision.intents:
            if intent.role is Role.HEAT_PRIMARY:
                if self.heat_executor is not None:
                    await self.heat_executor.async_apply(intent, force=force)
            else:
                await self.cool_executor.async_apply(intent, force=force)

    def _notify(self) -> None:
        if self._update_cb is not None:
            self._update_cb()

    # ---- event plumbing ------------------------------------------------

    @callback
    def _on_state_event(self, _event: Event[EventStateChangedData]) -> None:
        if self._debounce_unsub is not None:
            return
        self._debounce_unsub = async_call_later(
            self.hass, EVALUATION_DEBOUNCE, self._on_debounce
        )

    async def _on_debounce(self, _now: datetime) -> None:
        self._debounce_unsub = None
        await self._async_evaluate()

    async def _on_keep_alive(self, _now: datetime) -> None:
        await self._async_evaluate(force=True)

    def _cancel_debounce(self) -> None:
        if self._debounce_unsub is not None:
            self._debounce_unsub()
            self._debounce_unsub = None

    # ---- sensor fail-safe ----------------------------------------------

    def _manage_stale_timer(self, sensor_unusable: bool) -> None:
        timeout = float(self.options[CONF_SENSOR_STALE_TIMEOUT])
        if not sensor_unusable or timeout <= 0:
            self._cancel_stale_timer()
            return
        if self._stale_unsub is None:
            self._stale_unsub = async_call_later(self.hass, timeout, self._on_stale)

    async def _on_stale(self, _now: datetime) -> None:
        self._stale_unsub = None
        _LOGGER.warning(
            "%s has been unavailable for %s s; idling all sources for safety",
            self.sensor_entity_id,
            self.options[CONF_SENSOR_STALE_TIMEOUT],
        )
        decision = self.engine.force_idle(self._build_inputs())
        self.last_decision = decision
        await self._async_dispatch(decision, force=True)
        self._notify()

    def _cancel_stale_timer(self) -> None:
        if self._stale_unsub is not None:
            self._stale_unsub()
            self._stale_unsub = None

    # ---- misc ----------------------------------------------------------

    def _clamp(self, value: float) -> float:
        return min(
            max(value, float(self.options[CONF_MIN_TEMP])),
            float(self.options[CONF_MAX_TEMP]),
        )

    @property
    def min_temp(self) -> float:
        return float(self.options[CONF_MIN_TEMP])

    @property
    def max_temp(self) -> float:
        return float(self.options[CONF_MAX_TEMP])

    @property
    def temp_step(self) -> float:
        return float(self.options[CONF_TEMP_STEP])
