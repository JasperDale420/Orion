#!/usr/bin/env bash
# Wrapper for the dead-man watchdog — invoked every 5 minutes by
# ~/Library/LaunchAgents/com.empire.orion.deadman.plist.
#
# Same discipline as run_market_open_dataflow_check.sh / run_meta_search.sh:
#   - Uses the canonical uv path (~/.local/bin/uv). NEVER /opt/homebrew/bin/uv:
#     that path is absent on this host and made every fire exit 127 silently
#     (the 2026-05-22 orphan-close incident).
#   - Sets PATH explicitly (launchd starts with a near-empty PATH).
#   - Points DB_URL at the Docker-exposed host port (TimescaleDB on 5440). The
#     watchdog opens its OWN lightweight engine so it can read liveness/pipeline
#     state even when the main stack is wedged.
#   - Does NOT redirect output; launchd captures stdout/stderr per the plist.

set -euo pipefail

PROJECT_ROOT="/Users/jacobmcmillan/Empire/Orion"
UV_BIN="${HOME}/.local/bin/uv"

export PATH="/bin:/usr/bin:/opt/homebrew/bin:/usr/local/bin:${HOME}/.local/bin:${PATH}"

# Docker-side hostnames (`timescaledb`) are unreachable from the host; use the
# host-exposed port (compose maps it to localhost:5440).
export DB_URL="${DB_URL:-postgresql+asyncpg://orion:orion_password@localhost:5440/orion_db}"  # pragma: allowlist secret

export EMPIRE_LOG_DIR="${PROJECT_ROOT}/logs"
export EMPIRE_LOG_FORMAT="${EMPIRE_LOG_FORMAT:-json}"

cd "${PROJECT_ROOT}"

exec "${UV_BIN}" run python -m orion.jobs.deadman_watchdog
