# area-thermostat — everything lives in mise tasks: the common + python
# archetype tasks (ruff fmt/lint, pytest with coverage via uv, prose/license/
# shell/workflow lint) plus pinned tools come from the shared toolchain
# submodule at .mise/, selected in the root mise.toml.
# This Makefile is only the thin forwarding shim — `make <task>` == `mise run <task>`.
include .mise/archetypes/python/include.mk
