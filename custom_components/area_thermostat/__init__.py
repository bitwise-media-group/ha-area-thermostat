# Copyright 2026 BitWise Media Group Ltd
# SPDX-License-Identifier: MIT

"""Area Thermostat: one thermostat per area, arbitrating real climate devices.

Replaces the helpers+blueprint stack (input_number target, input_boolean
enable, area_climate_control automation) with a single config entry per area
producing a single climate entity that leads with underfloor heating and
brings the aircon in for cooling, boost heat, or fallback heat.
"""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .controller import AreaThermostatConfigEntry, AreaThermostatController

PLATFORMS = [Platform.CLIMATE]


async def async_setup_entry(
    hass: HomeAssistant, entry: AreaThermostatConfigEntry
) -> bool:
    """Set up an area thermostat from a config entry."""
    entry.runtime_data = AreaThermostatController(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: AreaThermostatConfigEntry
) -> bool:
    """Unload an area thermostat config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # The entity already stopped the controller on removal; stopping is
        # idempotent, so cover the paths where the entity never loaded.
        await entry.runtime_data.async_stop()
    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: AreaThermostatConfigEntry
) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
