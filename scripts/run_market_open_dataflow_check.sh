#!/usr/bin/env bash
# Wrapper for the market-open data-flow check — invoked a few minutes after
# the US cash open by ~/Library/LaunchAgents/
# com.empire.orion.market-open-dataflow-check.plist.
#
# Intentionally minimal — same discipline as run_launchd_health_probe.sh:
#   - Uses the canonical uv path (~/.local/bin/uv) to avoid the 2026-05-22
#     footgun where a plist hardcoded /opt/homebrew/bin/uv (absent on this
#     host) and silently exited 127 every fire.
#   - Sets PATH so the spawned check can find `docker` and `curl` (launchd
#     starts with a near-empty PATH). Docker Desktop installs its CLI shim
#     under /usr/local/bin; Homebrew under /opt/homebrew/bin.
#   - Does NOT redirect output; launchd captures stdout/stderr per the
#     plist's StandardOutPath/StandardErrorPath. The check writes its
#     structured alert rows to logs/market_open_dataflow_check.log directly.

set -euo pipefail

PROJECT_ROOT="/Users/jacobmcmillan/Empire/Orion"
UV_BIN="${HOME}/.local/bin/uv"

export PATH="/bin:/usr/bin:/opt/homebrew/bin:/usr/local/bin:${HOME}/.local/bin:${PATH}"

cd "${PROJECT_ROOT}"

exec "${UV_BIN}" run python -m orion.jobs.market_open_dataflow_check
