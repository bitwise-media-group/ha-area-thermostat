# Copyright 2026 BitWise Media Group Ltd
# SPDX-License-Identifier: MIT

"""Diagnostics for Area Thermostat config entries."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from .controller import AreaThermostatConfigEntry
from .sources import SourceExecutor


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AreaThermostatConfigEntry
) -> dict[str, Any]:
    """Return the full arbitration picture for one area."""
    controller = entry.runtime_data
    decision = controller.last_decision
    return {
        "data": dict(entry.data),
        "options": dict(entry.options),
        "state": {
            "mode": controller.mode.value,
            "last_non_off_mode": controller.last_non_off_mode.value,
            "target": controller.target,
            "target_low": controller.target_low,
            "target_high": controller.target_high,
            "current_temperature": controller.current_temperature(),
            "latches": controller.engine.snapshot(),
            "last_decision": asdict(decision) if decision is not None else None,
        },
        "sources": {
            "cool": _observed(hass, controller.cool_executor),
            "heat": _observed(hass, controller.heat_executor)
            if controller.heat_executor is not None
            else None,
        },
    }


def _observed(hass: HomeAssistant, executor: SourceExecutor) -> dict[str, Any]:
    state = hass.states.get(executor.source.entity_id)
    return {
        "entity_id": executor.source.entity_id,
        "roles": sorted(role.value for role in executor.source.roles),
        "strategy": executor.source.strategy,
        "state": state.state if state is not None else None,
        "preset_mode": state.attributes.get("preset_mode") if state else None,
        "temperature": state.attributes.get("temperature") if state else None,
        "hvac_action": state.attributes.get("hvac_action") if state else None,
    }
