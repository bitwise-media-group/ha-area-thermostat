# Copyright 2026 BitWise Media Group Ltd
# SPDX-License-Identifier: MIT

"""Constants for the Area Thermostat integration."""

from __future__ import annotations

DOMAIN = "area_thermostat"

# Config entry data (structure — what the area is made of).
CONF_TEMP_SENSOR = "temperature_sensor"
CONF_COOL_ENTITY = "cool_entity"
CONF_COOL_CAN_HEAT = "cool_can_heat"
CONF_HEAT_ENTITY = "heat_entity"
CONF_HEAT_STRATEGY = "heat_strategy"
CONF_HEAT_ACTIVE_PRESET = "heat_active_preset"
CONF_HEAT_IDLE_PRESET = "heat_idle_preset"

# Options (tuning).
CONF_ACT_DELTA = "act_delta"
CONF_IDLE_DELTA = "idle_delta"
CONF_BOOST_DELTA = "boost_delta"
CONF_MIN_TEMP = "min_temp"
CONF_MAX_TEMP = "max_temp"
CONF_TEMP_STEP = "temp_step"
CONF_KEEP_ALIVE = "keep_alive"
CONF_MIN_COMMAND_INTERVAL = "min_command_interval"
CONF_SENSOR_STALE_TIMEOUT = "sensor_stale_timeout"

STRATEGY_PRESET = "preset"
STRATEGY_HVAC_MODE = "hvac_mode"

DEFAULT_ACTIVE_PRESET = "home"
DEFAULT_IDLE_PRESET = "standby"

DEFAULT_ACT_DELTA = 1.5
DEFAULT_IDLE_DELTA = 0.5
DEFAULT_BOOST_DELTA = 3.0
DEFAULT_MIN_TEMP = 7.0
DEFAULT_MAX_TEMP = 30.0
DEFAULT_TEMP_STEP = 0.5
DEFAULT_KEEP_ALIVE = 300
DEFAULT_MIN_COMMAND_INTERVAL = 10
DEFAULT_SENSOR_STALE_TIMEOUT = 900

DEFAULT_OPTIONS = {
    CONF_ACT_DELTA: DEFAULT_ACT_DELTA,
    CONF_IDLE_DELTA: DEFAULT_IDLE_DELTA,
    CONF_BOOST_DELTA: DEFAULT_BOOST_DELTA,
    CONF_MIN_TEMP: DEFAULT_MIN_TEMP,
    CONF_MAX_TEMP: DEFAULT_MAX_TEMP,
    CONF_TEMP_STEP: DEFAULT_TEMP_STEP,
    CONF_KEEP_ALIVE: DEFAULT_KEEP_ALIVE,
    CONF_MIN_COMMAND_INTERVAL: DEFAULT_MIN_COMMAND_INTERVAL,
    CONF_SENSOR_STALE_TIMEOUT: DEFAULT_SENSOR_STALE_TIMEOUT,
}

# Enforced gap between target_temp_low and target_temp_high so the heat and
# cool latches can never be simultaneously true (see engine hysteresis).
MIN_RANGE_GAP = 1.0

# Default setpoints for a fresh entry (before the user or a restore touches
# them): the blueprint era ran the whole house at a single 21° target.
DEFAULT_TARGET = 21.0
DEFAULT_TARGET_LOW = 19.5
DEFAULT_TARGET_HIGH = 22.5

# Seconds state-change bursts are coalesced before an evaluation runs; also
# swallows the echoes of our own commands bouncing back off the sources.
EVALUATION_DEBOUNCE = 1.0

# Tolerance when comparing an intended setpoint against a device's reported
# one — differences below this are considered in sync (no command sent).
SETPOINT_TOLERANCE = 0.05
