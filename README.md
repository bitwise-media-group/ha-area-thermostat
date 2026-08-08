# Area Thermostat

A Home Assistant custom integration that presents **one real thermostat per area** while arbitrating the room's actual
climate devices behind it: underfloor heating leads, the air conditioner cools, and the air conditioner also steps in as
**boost heat** (when the room is far below target) or **fallback heat** (when the room has no working underfloor
heating).

One config entry per area produces one `climate` entity with proper `heat` / `cool` / `heat_cool` / `off` semantics, a
low/high target range, and a live `hvac_action` — which is exactly what HomeKit's Heater Cooler accessory and Control4's
thermostat proxy want to see.

## Why

The predecessor was an `input_number` + `input_boolean` + blueprint-automation stack per area. It worked, but it could
not model "heat only", "cool only", or a per-room setpoint range, and nothing about it looked like a thermostat to
HomeKit. This integration ports that blueprint's control policy into a tested state machine and gives it a first-class
entity.

## Behavior

- **Modes.** `heat_cool` runs full arbitration; `heat` never cools; `cool` never heats (no boost, no fallback); `off`
  idles everything (aircon off, heating to its idle preset).
- **Thresholds.** In `heat_cool` the `[low, high]` range is the dead band: heat engages below `low`, cooling above
  `high`, and each releases back inside the range — by default all the way to the range midpoint, tracking the range as
  it changes. An explicit release hysteresis overrides that, clamped to half the current gap. In single-target modes the
  blueprint's bands apply: engage 1.5 °C beyond the target, release within the hysteresis (default 0.5 °C).
- **Boost.** More than 3 °C below the heat threshold, the aircon joins in `heat` mode. The boost is deliberately sticky:
  it runs until the heat call itself releases, so it cannot chatter around the boost threshold.
- **Fallback.** If the area has no primary heat entity, or its entity is `unavailable`/`unknown`, the aircon takes over
  heating entirely.
- **Setpoint mirroring.** A setpoint is pushed to every device on each evaluation, so the devices self-limit even if
  Home Assistant stops driving them. In `heat_cool` an active call mirrors its release temperature rather than the range
  edge — otherwise the device's own thermostat would cut out at the edge before the area sensor reaches the release
  point. A keep-alive pass (default 5 min) re-asserts modes, presets, and setpoints to heal manual fiddling at the wall
  stat.
- **Fail-safe.** If the temperature sensor goes stale (default 15 min), every source is idled rather than left running
  on old data.
- `hvac_action` reports the arbitration intent (heating/cooling/idle), not the devices' compressor/burner state —
  deterministic, and what the bridges need.

## Device support

Sources are commanded through the standard `climate` services, with a per-source strategy instead of device adapters:

- **hvac_mode** — `climate.set_hvac_mode` heat/cool/off (CoolMasterNet and most aircons).
- **preset** — `climate.set_preset_mode` with configurable active/idle preset names, default `home`/`standby` (Heatmiser
  Neo via [heatmiserneo](https://mindrustuk.github.io/Heatmiser-for-home-assistant/)).

Anything speaking either dialect should work unmodified.

## Install

1. HACS → Integrations → ⋮ → _Custom repositories_ → add this repository (category: integration).
2. Install **Area Thermostat** and restart Home Assistant.
3. Settings → Devices & Services → _Add integration_ → Area Thermostat. Pick the room's temperature sensor, the aircon,
   and (optionally) the underfloor heating stat; tune the bands under _Configure_ afterwards.

### Migrating from the blueprint stack

Per area: add a config entry, verify it drives the devices, then **disable that area's `area_climate_control`
automation** (two controllers will fight), and finally delete the orphaned target/enable helpers.

### HomeKit

In the HomeKit Bridge accessory settings, expose the area thermostat and pick the **Heater Cooler** accessory type — the
`heat_cool` low/high targets map onto its heating/cooling threshold dials.

## Development

Everything runs through [mise](https://mise.jdx.dev) tasks backed by the shared toolchain submodule
(`git submodule update --init` after cloning):

```sh
make fmt   # ruff format + prose format + license headers
make lint  # ruff + prose/shell/container/license checks
make test  # pytest (engine unit tests + HA integration tests via uv)
make pr    # the full local gate
```

The control policy lives in `custom_components/area_thermostat/engine.py` as a pure-Python state machine with no Home
Assistant imports — start there. Releases are cut by release-please from Conventional Commits; the release PR bumps
`version.txt`, the integration `manifest.json`, and `pyproject.toml`.
