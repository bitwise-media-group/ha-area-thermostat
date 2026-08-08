# Copyright 2026 BitWise Media Group Ltd
# SPDX-License-Identifier: MIT

"""Pure-Python arbitration engine for an area's heating and cooling sources.

This module deliberately imports nothing from Home Assistant so the whole
control policy is unit-testable with plain pytest. The enums mirror the
string values of HA's HVACMode/HVACAction where they overlap.

The engine holds exactly three pieces of mutable state — the call latches —
and everything else is derived per evaluation:

- ``heat_call``:     the area is calling for heat.
- ``cool_call``:     the area is calling for cooling.
- ``aux_heat_call``: the auxiliary heat source (the aircon) is engaged, either
  as a boost (temperature far below the heat threshold) or as a fallback
  (no usable primary heat source). Sticky by design: once engaged it is
  released only when the heat call itself releases, so it never chatters
  around the boost threshold.

Hysteresis model (ported from the area_climate_control blueprint):

- HEAT/COOL single-target modes: engage ``act_delta`` beyond the target,
  release ``idle_delta`` from it (defaults 1.5/0.5 — the blueprint's bands).
- HEAT_COOL: the user's [low, high] range IS the dead band, so heat engages
  the moment the temperature drops below ``low`` and cool the moment it rises
  above ``high`` (this is what makes HomeKit's Heater Cooler threshold
  semantics literal); each releases back inside the range — by default all
  the way to the range midpoint. An explicit ``idle_delta`` overrides the
  release distance, clamped to half the current gap per evaluation so the
  release points can at worst meet at the midpoint and never cross. While a
  call is active its release temperature (not the range edge) is mirrored to
  the device, so the device's internal thermostat cannot cut out at the edge
  before the area sensor reaches the release point.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "Action",
    "ClimateEngine",
    "Decision",
    "Direction",
    "EngineConfig",
    "EngineInputs",
    "Mode",
    "Role",
    "SourceIntent",
]


class Mode(StrEnum):
    """Operating mode; values mirror HA's HVACMode."""

    OFF = "off"
    HEAT = "heat"
    COOL = "cool"
    HEAT_COOL = "heat_cool"


class Action(StrEnum):
    """Arbitration outcome; values mirror HA's HVACAction."""

    OFF = "off"
    IDLE = "idle"
    HEATING = "heating"
    COOLING = "cooling"


class Role(StrEnum):
    """What a source contributes to the area."""

    HEAT_PRIMARY = "heat_primary"
    HEAT_BOOST = "heat_boost"
    COOL = "cool"


class Direction(StrEnum):
    """Which way an active source is being driven."""

    HEAT = "heat"
    COOL = "cool"


# Release band for single-target modes when idle_delta is unset — the
# blueprint's 0.5 °C.
_SINGLE_TARGET_IDLE_DEFAULT = 0.5


@dataclass(frozen=True, slots=True)
class EngineConfig:
    """Tuning knobs; defaults reproduce the blueprint's bands.

    ``idle_delta`` of ``None`` (the default) means auto: release at the
    low/high midpoint in HEAT_COOL, and 0.5 °C from the target in the
    single-target modes. An explicit value fixes the release distance
    (clamped to half the current gap in HEAT_COOL).
    """

    act_delta: float = 1.5
    idle_delta: float | None = None
    boost_delta: float = 3.0


@dataclass(frozen=True, slots=True)
class EngineInputs:
    """A snapshot of the world for one evaluation."""

    mode: Mode
    current_temp: float | None
    target: float | None = None
    target_low: float | None = None
    target_high: float | None = None
    has_heat_primary: bool = False
    heat_primary_available: bool = False
    has_heat_boost: bool = False


@dataclass(frozen=True, slots=True)
class SourceIntent:
    """What a role's source should be doing right now.

    ``setpoint`` is mirrored to the device so it self-limits — the safety
    backstop if this engine ever stops being consulted. Idle sources get the
    plain target; an active call in HEAT_COOL gets its release temperature
    instead, so the device keeps running until the engine releases. ``None``
    means leave the device setpoint alone (OFF mode).
    """

    role: Role
    active: bool
    direction: Direction | None
    setpoint: float | None


@dataclass(frozen=True, slots=True)
class Decision:
    """Result of one evaluation.

    ``valid`` is False when the inputs were unusable (no temperature); the
    latches are left untouched and ``intents`` is empty so the caller holds
    the current device state rather than acting on garbage.
    """

    action: Action
    heat_call: bool
    cool_call: bool
    aux_heat_call: bool
    intents: tuple[SourceIntent, ...]
    valid: bool


@dataclass(frozen=True, slots=True)
class _Thresholds:
    heat_on: float
    heat_off: float
    cool_on: float
    cool_off: float
    boost_on: float


class ClimateEngine:
    """The blueprint's arbitration, as a latching state machine."""

    def __init__(self, config: EngineConfig | None = None) -> None:
        self._config = config or EngineConfig()
        self._heat_call = False
        self._cool_call = False
        self._aux_heat_call = False

    @property
    def heat_call(self) -> bool:
        return self._heat_call

    @property
    def cool_call(self) -> bool:
        return self._cool_call

    @property
    def aux_heat_call(self) -> bool:
        return self._aux_heat_call

    def snapshot(self) -> dict[str, bool]:
        """Latch state for persistence (extra_state_attributes / restore)."""
        return {
            "heat_call": self._heat_call,
            "cool_call": self._cool_call,
            "aux_heat_call": self._aux_heat_call,
        }

    def restore(
        self,
        *,
        heat_call: bool = False,
        cool_call: bool = False,
        aux_heat_call: bool = False,
    ) -> None:
        """Reinstate latches saved before a restart."""
        self._heat_call = heat_call
        self._cool_call = cool_call
        self._aux_heat_call = aux_heat_call

    def force_idle(self, inputs: EngineInputs) -> Decision:
        """Drop every call and idle all sources (sensor-stale fail-safe)."""
        self._heat_call = False
        self._cool_call = False
        self._aux_heat_call = False
        action = Action.OFF if inputs.mode is Mode.OFF else Action.IDLE
        return Decision(
            action=action,
            heat_call=False,
            cool_call=False,
            aux_heat_call=False,
            intents=self._build_intents(inputs, setpoints=None),
            valid=True,
        )

    def evaluate(self, inputs: EngineInputs) -> Decision:
        """Update the latches from a fresh snapshot and emit intents."""
        if inputs.mode is Mode.OFF:
            return self.force_idle(inputs)

        thresholds = self._thresholds(inputs)
        if thresholds is None or inputs.current_temp is None:
            # Unusable inputs: hold — report current latches, command nothing.
            return Decision(
                action=self._action(inputs.mode),
                heat_call=self._heat_call,
                cool_call=self._cool_call,
                aux_heat_call=self._aux_heat_call,
                intents=(),
                valid=False,
            )

        self._update_latches(inputs, thresholds)
        return Decision(
            action=self._action(inputs.mode),
            heat_call=self._heat_call,
            cool_call=self._cool_call,
            aux_heat_call=self._aux_heat_call,
            intents=self._build_intents(
                inputs, setpoints=self._setpoints(inputs, thresholds)
            ),
            valid=True,
        )

    def _thresholds(self, inputs: EngineInputs) -> _Thresholds | None:
        cfg = self._config
        if inputs.mode is Mode.HEAT_COOL:
            low, high = inputs.target_low, inputs.target_high
            if low is None or high is None:
                return None
            # Auto (None) releases at the midpoint; an explicit value is
            # clamped to half the gap so the release points at worst meet at
            # the midpoint, never cross, however narrow the range is dragged.
            half_gap = max((high - low) / 2, 0.0)
            idle = half_gap if cfg.idle_delta is None else min(cfg.idle_delta, half_gap)
            return _Thresholds(
                heat_on=low,
                heat_off=low + idle,
                cool_on=high,
                cool_off=high - idle,
                boost_on=low - cfg.boost_delta,
            )
        target = inputs.target
        if target is None:
            return None
        idle = _SINGLE_TARGET_IDLE_DEFAULT if cfg.idle_delta is None else cfg.idle_delta
        return _Thresholds(
            heat_on=target - cfg.act_delta,
            heat_off=target - idle,
            cool_on=target + cfg.act_delta,
            cool_off=target + idle,
            boost_on=target - cfg.boost_delta,
        )

    def _update_latches(self, inputs: EngineInputs, th: _Thresholds) -> None:
        temp = inputs.current_temp
        assert temp is not None
        heat_allowed = inputs.mode in (Mode.HEAT, Mode.HEAT_COOL) and (
            inputs.has_heat_primary or inputs.has_heat_boost
        )
        cool_allowed = inputs.mode in (Mode.COOL, Mode.HEAT_COOL)

        if not heat_allowed:
            self._heat_call = False
        elif temp < th.heat_on:
            self._heat_call = True
        elif temp >= th.heat_off:
            self._heat_call = False

        if not cool_allowed:
            self._cool_call = False
        elif temp > th.cool_on:
            self._cool_call = True
        elif temp <= th.cool_off:
            self._cool_call = False

        # Range validation upstream keeps these mutually exclusive; if a
        # misconfiguration slips through, resolve by which side of the band's
        # midpoint the temperature sits on rather than fighting ourselves.
        if self._heat_call and self._cool_call:
            midpoint = (th.heat_on + th.cool_on) / 2
            self._heat_call = temp < midpoint
            self._cool_call = not self._heat_call

        # The sticky aux latch: engaged by boost or by an unusable primary,
        # released only when the heat call itself releases.
        primary_usable = inputs.has_heat_primary and inputs.heat_primary_available
        if not self._heat_call or not inputs.has_heat_boost:
            self._aux_heat_call = False
        elif temp < th.boost_on or not primary_usable:
            self._aux_heat_call = True

    def _action(self, mode: Mode) -> Action:
        if mode is Mode.OFF:
            return Action.OFF
        if self._heat_call:
            return Action.HEATING
        if self._cool_call:
            return Action.COOLING
        return Action.IDLE

    def _setpoints(
        self, inputs: EngineInputs, th: _Thresholds
    ) -> tuple[float | None, float | None]:
        """(heat_setpoint, cool_setpoint) to mirror to the devices.

        In HEAT_COOL an active call mirrors its release temperature rather
        than the range edge: with the edge as setpoint the device's own
        thermostat cuts out at the edge before the engine's release point and
        the area bounces along that edge. Idle sources fall back to the plain
        targets as the self-limiting backstop. Single-target modes are safe
        as-is — there the target sits beyond the release point.
        """
        if inputs.mode is Mode.HEAT_COOL:
            heat_setpoint = th.heat_off if self._heat_call else inputs.target_low
            cool_setpoint = th.cool_off if self._cool_call else inputs.target_high
            return heat_setpoint, cool_setpoint
        return inputs.target, inputs.target

    def _build_intents(
        self,
        inputs: EngineInputs,
        setpoints: tuple[float | None, float | None] | None,
    ) -> tuple[SourceIntent, ...]:
        heat_setpoint, cool_setpoint = setpoints if setpoints else (None, None)
        intents: list[SourceIntent] = []
        if inputs.has_heat_primary:
            intents.append(
                SourceIntent(
                    role=Role.HEAT_PRIMARY,
                    active=self._heat_call,
                    direction=Direction.HEAT if self._heat_call else None,
                    setpoint=heat_setpoint,
                )
            )
        if self._aux_heat_call:
            cool_unit = SourceIntent(
                role=Role.COOL,
                active=True,
                direction=Direction.HEAT,
                setpoint=heat_setpoint,
            )
        elif self._cool_call:
            cool_unit = SourceIntent(
                role=Role.COOL,
                active=True,
                direction=Direction.COOL,
                setpoint=cool_setpoint,
            )
        else:
            cool_unit = SourceIntent(
                role=Role.COOL,
                active=False,
                direction=None,
                setpoint=cool_setpoint,
            )
        intents.append(cool_unit)
        return tuple(intents)
