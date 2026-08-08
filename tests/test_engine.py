# Copyright 2026 BitWise Media Group Ltd
# SPDX-License-Identifier: MIT

"""Unit tests for the pure arbitration engine — no Home Assistant involved."""

from __future__ import annotations

import pytest

from custom_components.area_thermostat.engine import (
    Action,
    ClimateEngine,
    Direction,
    EngineConfig,
    EngineInputs,
    Mode,
    Role,
    SourceIntent,
)


def make_inputs(
    temp: float | None,
    *,
    mode: Mode = Mode.HEAT_COOL,
    target: float = 21.0,
    low: float = 19.0,
    high: float = 23.0,
    has_primary: bool = True,
    primary_available: bool = True,
    has_boost: bool = True,
) -> EngineInputs:
    return EngineInputs(
        mode=mode,
        current_temp=temp,
        target=target,
        target_low=low,
        target_high=high,
        has_heat_primary=has_primary,
        heat_primary_available=primary_available,
        has_heat_boost=has_boost,
    )


def cool_intent(decision) -> SourceIntent:
    return next(i for i in decision.intents if i.role is Role.COOL)


def primary_intent(decision) -> SourceIntent:
    return next(i for i in decision.intents if i.role is Role.HEAT_PRIMARY)


class TestSingleTargetBands:
    """HEAT/COOL modes reproduce the blueprint's ±1.5/±0.5 bands exactly."""

    @pytest.mark.parametrize(
        ("temp", "action"),
        [
            (19.4, Action.HEATING),  # below 21 - 1.5
            (19.6, Action.IDLE),  # inside the dead band: no engage
            (21.0, Action.IDLE),
            (22.4, Action.IDLE),  # HEAT mode never cools
            (25.0, Action.IDLE),
        ],
    )
    def test_heat_mode_engagement(self, temp: float, action: Action) -> None:
        engine = ClimateEngine()
        decision = engine.evaluate(make_inputs(temp, mode=Mode.HEAT))
        assert decision.action is action

    def test_heat_mode_releases_at_idle_delta(self) -> None:
        engine = ClimateEngine()
        assert (
            engine.evaluate(make_inputs(19.0, mode=Mode.HEAT)).action is Action.HEATING
        )
        # Still heating through the dead band on the way back up.
        assert (
            engine.evaluate(make_inputs(20.0, mode=Mode.HEAT)).action is Action.HEATING
        )
        # Released once within idle_delta of target.
        assert engine.evaluate(make_inputs(20.6, mode=Mode.HEAT)).action is Action.IDLE

    def test_cool_mode_engage_and_release(self) -> None:
        engine = ClimateEngine()
        assert (
            engine.evaluate(make_inputs(23.0, mode=Mode.COOL)).action is Action.COOLING
        )
        assert (
            engine.evaluate(make_inputs(22.0, mode=Mode.COOL)).action is Action.COOLING
        )
        assert engine.evaluate(make_inputs(21.4, mode=Mode.COOL)).action is Action.IDLE

    def test_hysteresis_produces_one_transition_each_way(self) -> None:
        engine = ClimateEngine()
        transitions = []
        last = None
        for temp in (21.0, 19.4, 19.6, 19.4, 20.4, 20.6, 20.4, 21.0):
            action = engine.evaluate(make_inputs(temp, mode=Mode.HEAT)).action
            if action is not last:
                transitions.append(action)
                last = action
        assert transitions == [Action.IDLE, Action.HEATING, Action.IDLE]


class TestHeatCoolRange:
    """HEAT_COOL: [low, high] is the dead band; idle_delta on release."""

    def test_heat_engages_below_low(self) -> None:
        engine = ClimateEngine()
        assert engine.evaluate(make_inputs(18.9)).action is Action.HEATING
        assert engine.evaluate(make_inputs(19.2)).action is Action.HEATING  # sticky
        assert engine.evaluate(make_inputs(19.5)).action is Action.IDLE

    def test_cool_engages_above_high(self) -> None:
        engine = ClimateEngine()
        assert engine.evaluate(make_inputs(23.1)).action is Action.COOLING
        assert engine.evaluate(make_inputs(22.8)).action is Action.COOLING  # sticky
        assert engine.evaluate(make_inputs(22.5)).action is Action.IDLE

    def test_inside_band_is_idle(self) -> None:
        engine = ClimateEngine()
        for temp in (19.0, 20.0, 21.0, 22.0, 23.0):
            assert engine.evaluate(make_inputs(temp)).action is Action.IDLE

    def test_swing_from_heat_to_cool(self) -> None:
        engine = ClimateEngine()
        assert engine.evaluate(make_inputs(18.0)).action is Action.HEATING
        decision = engine.evaluate(make_inputs(24.0))
        assert decision.action is Action.COOLING
        assert decision.heat_call is False

    def test_large_idle_delta_releases_at_the_midpoint(self) -> None:
        # idle_delta 5 with a 4° range (19-23) clamps to 2: both releases
        # land exactly on the midpoint (21) and never cross.
        engine = ClimateEngine(EngineConfig(act_delta=6.0, idle_delta=5.0))
        assert engine.evaluate(make_inputs(23.5)).action is Action.COOLING
        assert engine.evaluate(make_inputs(21.1)).action is Action.COOLING
        assert engine.evaluate(make_inputs(21.0)).action is Action.IDLE
        assert engine.evaluate(make_inputs(18.5)).action is Action.HEATING
        assert engine.evaluate(make_inputs(20.9)).action is Action.HEATING
        assert engine.evaluate(make_inputs(21.0)).action is Action.IDLE

    def test_moderate_idle_delta_is_not_clamped(self) -> None:
        engine = ClimateEngine(EngineConfig(act_delta=2.5, idle_delta=1.5))
        assert engine.evaluate(make_inputs(23.5)).action is Action.COOLING
        # Release at high - 1.5 = 21.5, above the midpoint.
        assert engine.evaluate(make_inputs(21.6)).action is Action.COOLING
        assert engine.evaluate(make_inputs(21.5)).action is Action.IDLE


class TestStickyBoost:
    """The aux latch: boost below -boost_delta, held until the call releases."""

    def test_boost_engages_and_stays_through_recovery(self) -> None:
        engine = ClimateEngine()
        # 3° below low (19) -> boost point is 16.
        decision = engine.evaluate(make_inputs(15.9))
        assert decision.aux_heat_call is True
        assert cool_intent(decision).direction is Direction.HEAT
        # Recovering through the band the blueprint left the boost running in.
        decision = engine.evaluate(make_inputs(17.5))
        assert decision.aux_heat_call is True
        decision = engine.evaluate(make_inputs(19.2))
        assert decision.aux_heat_call is True  # heat call still latched
        # Released only when the heat call itself releases.
        decision = engine.evaluate(make_inputs(19.5))
        assert decision.aux_heat_call is False
        assert cool_intent(decision).active is False

    def test_no_boost_without_boost_source(self) -> None:
        engine = ClimateEngine()
        decision = engine.evaluate(make_inputs(15.0, has_boost=False))
        assert decision.action is Action.HEATING
        assert decision.aux_heat_call is False

    def test_mild_heat_call_does_not_boost(self) -> None:
        engine = ClimateEngine()
        decision = engine.evaluate(make_inputs(18.5))
        assert decision.heat_call is True
        assert decision.aux_heat_call is False
        assert cool_intent(decision).active is False


class TestFallback:
    """Aux heat when the primary is missing or offline."""

    def test_no_primary_configured(self) -> None:
        engine = ClimateEngine()
        decision = engine.evaluate(make_inputs(18.5, has_primary=False))
        assert decision.aux_heat_call is True
        assert cool_intent(decision).direction is Direction.HEAT
        assert all(i.role is not Role.HEAT_PRIMARY for i in decision.intents)

    def test_primary_unavailable_triggers_fallback(self) -> None:
        engine = ClimateEngine()
        decision = engine.evaluate(make_inputs(18.5, primary_available=False))
        assert decision.aux_heat_call is True

    def test_primary_returning_leaves_running_aux_heat(self) -> None:
        engine = ClimateEngine()
        engine.evaluate(make_inputs(18.5, primary_available=False))
        # The stat comes back mid-call: the aux stays until the call releases.
        decision = engine.evaluate(make_inputs(18.5, primary_available=True))
        assert decision.aux_heat_call is True
        assert primary_intent(decision).active is True
        decision = engine.evaluate(make_inputs(19.5, primary_available=True))
        assert decision.aux_heat_call is False


class TestModeConstraints:
    def test_cool_mode_never_heats(self) -> None:
        engine = ClimateEngine()
        decision = engine.evaluate(make_inputs(10.0, mode=Mode.COOL))
        assert decision.action is Action.IDLE
        assert decision.heat_call is False
        assert decision.aux_heat_call is False
        assert primary_intent(decision).active is False

    def test_heat_mode_never_cools(self) -> None:
        engine = ClimateEngine()
        decision = engine.evaluate(make_inputs(35.0, mode=Mode.HEAT))
        assert decision.cool_call is False
        assert cool_intent(decision).active is False

    def test_switching_to_cool_drops_heat_latches(self) -> None:
        engine = ClimateEngine()
        engine.evaluate(make_inputs(15.0))
        decision = engine.evaluate(make_inputs(15.0, mode=Mode.COOL))
        assert decision.heat_call is False
        assert decision.aux_heat_call is False

    def test_off_clears_everything(self) -> None:
        engine = ClimateEngine()
        engine.evaluate(make_inputs(15.0))
        decision = engine.evaluate(make_inputs(15.0, mode=Mode.OFF))
        assert decision.action is Action.OFF
        assert decision.heat_call is False
        assert decision.aux_heat_call is False
        assert cool_intent(decision).active is False
        assert primary_intent(decision).active is False
        # OFF leaves device setpoints alone.
        assert all(i.setpoint is None for i in decision.intents)

    def test_no_heat_sources_means_no_heat_call(self) -> None:
        engine = ClimateEngine()
        decision = engine.evaluate(
            make_inputs(10.0, mode=Mode.HEAT, has_primary=False, has_boost=False)
        )
        assert decision.action is Action.IDLE
        assert decision.heat_call is False


class TestSetpointMirroring:
    def test_single_target_mirrors_target_everywhere(self) -> None:
        engine = ClimateEngine()
        decision = engine.evaluate(make_inputs(19.0, mode=Mode.HEAT, target=21.0))
        assert primary_intent(decision).setpoint == 21.0
        assert cool_intent(decision).setpoint == 21.0

    def test_heat_cool_idle_mirrors_low_to_heaters_high_to_cooler(self) -> None:
        engine = ClimateEngine()
        decision = engine.evaluate(make_inputs(21.0))
        assert primary_intent(decision).setpoint == 19.0
        assert cool_intent(decision).setpoint == 23.0

    def test_active_cool_call_mirrors_its_release_temperature(self) -> None:
        # Setpoint = high would let the device's own thermostat cut out at
        # the top of the range before the engine's release point.
        engine = ClimateEngine()
        decision = engine.evaluate(make_inputs(24.0))
        assert cool_intent(decision).setpoint == 22.5
        # Back inside the range but still latched: keep driving to release.
        decision = engine.evaluate(make_inputs(22.8))
        assert decision.cool_call is True
        assert cool_intent(decision).setpoint == 22.5
        # Released: the plain target returns as the backstop.
        decision = engine.evaluate(make_inputs(22.5))
        assert decision.cool_call is False
        assert cool_intent(decision).setpoint == 23.0

    def test_active_heat_call_mirrors_its_release_temperature(self) -> None:
        engine = ClimateEngine()
        decision = engine.evaluate(make_inputs(18.5))
        assert primary_intent(decision).setpoint == 19.5
        decision = engine.evaluate(make_inputs(19.5))
        assert decision.heat_call is False
        assert primary_intent(decision).setpoint == 19.0

    def test_aux_heating_cool_unit_gets_the_heat_release_setpoint(self) -> None:
        engine = ClimateEngine()
        decision = engine.evaluate(make_inputs(15.0))
        assert cool_intent(decision).direction is Direction.HEAT
        assert cool_intent(decision).setpoint == 19.5


class TestUnusableInputs:
    def test_none_temperature_holds(self) -> None:
        engine = ClimateEngine()
        engine.evaluate(make_inputs(15.0))
        decision = engine.evaluate(make_inputs(None))
        assert decision.valid is False
        assert decision.intents == ()
        # Latches untouched.
        assert decision.heat_call is True
        assert decision.aux_heat_call is True

    def test_missing_targets_hold(self) -> None:
        engine = ClimateEngine()
        decision = engine.evaluate(
            EngineInputs(mode=Mode.HEAT, current_temp=15.0, target=None)
        )
        assert decision.valid is False

    def test_force_idle_clears_latches(self) -> None:
        engine = ClimateEngine()
        engine.evaluate(make_inputs(15.0))
        decision = engine.force_idle(make_inputs(None))
        assert decision.valid is True
        assert decision.action is Action.IDLE
        assert decision.heat_call is False
        assert cool_intent(decision).active is False


class TestPersistence:
    def test_snapshot_restore_round_trip(self) -> None:
        engine = ClimateEngine()
        engine.evaluate(make_inputs(15.0))
        snapshot = engine.snapshot()
        assert snapshot == {
            "heat_call": True,
            "cool_call": False,
            "aux_heat_call": True,
        }
        restored = ClimateEngine()
        restored.restore(**snapshot)
        # In the recovery band the restored engine keeps boosting, exactly
        # like the original would have.
        decision = restored.evaluate(make_inputs(17.5))
        assert decision.aux_heat_call is True
