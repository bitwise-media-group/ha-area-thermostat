# Copyright 2026 BitWise Media Group Ltd
# SPDX-License-Identifier: MIT

"""Source model: how an engine intent becomes climate service calls.

A source is an underlying ``climate`` entity plus the roles it plays and the
command strategy it understands:

- ``hvac_mode``: driven with ``climate.set_hvac_mode`` (heat/cool/off) — the
  CoolMasterNet aircon.
- ``preset``: driven with ``climate.set_preset_mode`` using configurable
  active/idle preset names (defaults home/standby) — the Heatmiser Neo stat.

``SourceExecutor`` keeps the underlying device honest without hammering it:
calls are deduped against the *observed* entity state (a no-op command is
never sent — except on a forced keep-alive pass, which re-asserts state to
heal wall-stat fiddling), and a per-entity throttle coalesces rapid intent
changes into the newest one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import SETPOINT_TOLERANCE, STRATEGY_HVAC_MODE, STRATEGY_PRESET
from .engine import Direction, Role, SourceIntent

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """An underlying climate entity and how to command it."""

    entity_id: str
    roles: frozenset[Role]
    strategy: str = STRATEGY_HVAC_MODE
    active_preset: str = "home"
    idle_preset: str = "standby"


class SourceExecutor:
    """Dispatches intents to one source with dedupe and throttling."""

    def __init__(
        self,
        hass: HomeAssistant,
        source: SourceConfig,
        min_interval: float,
    ) -> None:
        self._hass = hass
        self.source = source
        self._min_interval = min_interval
        self._last_sent: datetime | None = None
        self._pending: tuple[SourceIntent, bool] | None = None
        self._unsub_flush: CALLBACK_TYPE | None = None

    def stop(self) -> None:
        """Cancel any pending flush timer."""
        if self._unsub_flush is not None:
            self._unsub_flush()
            self._unsub_flush = None
        self._pending = None

    def is_available(self) -> bool:
        state = self._hass.states.get(self.source.entity_id)
        return state is not None and state.state not in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        )

    def observed_hvac_mode(self) -> str | None:
        state = self._hass.states.get(self.source.entity_id)
        return None if state is None else state.state

    async def async_apply(self, intent: SourceIntent, *, force: bool = False) -> None:
        """Bring the source in line with the intent (throttled, deduped)."""
        if not self.is_available():
            _LOGGER.debug(
                "%s is unavailable; skipping %s", self.source.entity_id, intent
            )
            return

        if self._min_interval > 0 and self._last_sent is not None:
            elapsed = (dt_util.utcnow() - self._last_sent).total_seconds()
            if elapsed < self._min_interval:
                # Too soon: park the newest intent and (re)arm one flush timer.
                self._pending = (intent, force)
                if self._unsub_flush is None:
                    self._unsub_flush = async_call_later(
                        self._hass, self._min_interval - elapsed, self._async_flush
                    )
                return

        await self._async_send(intent, force)

    async def _async_flush(self, _now: datetime) -> None:
        self._unsub_flush = None
        if self._pending is None:
            return
        intent, force = self._pending
        self._pending = None
        if self.is_available():
            await self._async_send(intent, force)

    async def _async_send(self, intent: SourceIntent, force: bool) -> None:
        sent = False
        for service, data in self._desired_calls(intent, force):
            _LOGGER.debug("%s -> %s %s", self.source.entity_id, service, data)
            await self._hass.services.async_call(
                CLIMATE_DOMAIN,
                service,
                {ATTR_ENTITY_ID: self.source.entity_id, **data},
                blocking=False,
            )
            sent = True
        if sent:
            self._last_sent = dt_util.utcnow()

    def _desired_calls(
        self, intent: SourceIntent, force: bool
    ) -> list[tuple[str, dict]]:
        state = self._hass.states.get(self.source.entity_id)
        assert state is not None  # is_available() checked by callers
        calls: list[tuple[str, dict]] = []

        if self.source.strategy == STRATEGY_PRESET:
            desired_preset = (
                self.source.active_preset if intent.active else self.source.idle_preset
            )
            observed = state.attributes.get(ATTR_PRESET_MODE)
            if force or observed != desired_preset:
                calls.append(
                    (SERVICE_SET_PRESET_MODE, {ATTR_PRESET_MODE: desired_preset})
                )
        else:
            desired_mode = self._desired_hvac_mode(intent)
            if force or state.state != desired_mode:
                calls.append((SERVICE_SET_HVAC_MODE, {ATTR_HVAC_MODE: desired_mode}))

        if intent.setpoint is not None:
            observed_setpoint = state.attributes.get(ATTR_TEMPERATURE)
            if (
                force
                or not isinstance(observed_setpoint, int | float)
                or abs(observed_setpoint - intent.setpoint) > SETPOINT_TOLERANCE
            ):
                calls.append(
                    (SERVICE_SET_TEMPERATURE, {ATTR_TEMPERATURE: intent.setpoint})
                )

        return calls

    @staticmethod
    def _desired_hvac_mode(intent: SourceIntent) -> str:
        if not intent.active:
            return HVACMode.OFF
        if intent.direction is Direction.HEAT:
            return HVACMode.HEAT
        return HVACMode.COOL
