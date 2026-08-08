# area-thermostat — everything lives in mise tasks: the markdown-lib archetype
# (prose/license/shell lint) plus pinned tools come from the shared toolchain
# submodule at .mise/, selected in the root mise.toml; tasks.toml carries the
# Python-specific tasks (ruff fmt/lint, pytest with coverage via uv) and the
# canonical fmt/lint/test gates.
# This Makefile is only the thin forwarding shim — `make <task>` == `mise run <task>`.
include .mise/mise.mk
