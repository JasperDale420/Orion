#!/usr/bin/env bash
# Native runner for orion.main_meta --scheduled — the daily meta-search
# evolution loop. Replaces the never-brought-up Docker `meta-search`
# service (compose `scheduled` profile; see
# proposals/2026-06-10-eod-meta-diagnosis.md root-cause #2: meta-search
# had NO production scheduler — the `scheduled` profile is never `up`).
#
# Shape: long-running KeepAlive daemon, NOT a one-shot. main_meta.py's
# `--scheduled` mode is an internal poll loop (60s tick) that self-fires
# at 18:00 ET on weekdays and otherwise sleeps. The process owns its own
# schedule, so launchd's job is just to keep it alive — mirroring the
# ingestion/execution native daemons rather than the orphan-close
# StartCalendarInterval one-shot.
#
# Connection strings target the Docker-exposed host ports (TimescaleDB on
# 5440). The AI gateway is left to config defaults (http://localhost:8002/v1),
# which is the native ai-gateway endpoint (cli-proxy LISTENING on :8002).
#
# Lifecycle is managed by launchd via
# ~/Library/LaunchAgents/com.empire.orion.meta-search.plist. KeepAlive
# replaces Docker's `restart: unless-stopped`; ThrottleInterval replaces
# the implicit Docker restart delay.
#
# Restart safely with:
#   launchctl kickstart -k gui/$(id -u)/com.empire.orion.meta-search
# The final line exec's the venv python directly (NOT `uv run`) so a
# SIGKILL reaches python and leaves no orphan.

set -euo pipefail

PROJECT_ROOT="/Users/jacobmcmillan/Empire/Orion"
LOG_DIR="${PROJECT_ROOT}/logs"
LOG_FILE="${LOG_DIR}/meta_search_native.log"

mkdir -p "${LOG_DIR}"

# uv lives in ~/.local/bin (default install path); homebrew bins on Apple
# Silicon live under /opt/homebrew. launchd starts with a near-empty PATH
# so we have to set this explicitly. NEVER reference /opt/homebrew/bin/uv
# directly — uv is at ~/.local/bin/uv on this host (the orphan-close
# exit-127 incident, 2026-05-22).
export PATH="/opt/homebrew/bin:/usr/local/bin:${HOME}/.local/bin:${PATH}"

# --- Service-to-service connection strings -----------------------------
# Docker-side hostnames (`timescaledb`) are unreachable from the host; use
# the host-exposed port (compose maps it to localhost:5440).
export DB_URL="postgresql+asyncpg://orion:orion_password@localhost:5440/orion_db"  # pragma: allowlist secret

# Heber parquet cache on the host (read by MetaSearchAgent backtests).
export HEBER_DATA_ROOT="${HOME}/.heber-cache/data"

# --- Orion runtime config ----------------------------------------------
export ORION_RUN_ID="${ORION_RUN_ID:-native_meta_search}"

# Base solver to evolve. Mirrors the docker-compose default.
export ORION_META_BASE_SOLVER="${ORION_META_BASE_SOLVER:-diversified_baseline_v1}"

# Logging: empire_core writes structured JSON to a daily-rotating file in
# EMPIRE_LOG_DIR; point it at the project logs dir.
export EMPIRE_LOG_DIR="${LOG_DIR}"
export EMPIRE_LOG_FORMAT="${EMPIRE_LOG_FORMAT:-json}"

cd "${PROJECT_ROOT}"

# Ensure the project virtualenv (.venv) exists and dependencies are current.
# Tolerate a transient sync failure when a usable venv already exists, but
# refuse to start if there is no interpreter at all.
if ! uv sync; then
    if [[ ! -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
        echo "FATAL: uv sync failed and no interpreter at ${PROJECT_ROOT}/.venv/bin/python" >&2
        exit 1
    fi
    echo "WARN: uv sync failed; starting with existing .venv" >&2
fi

# exec the venv's python DIRECTLY — never `uv run python` (uv would spawn
# python as a child and a kickstart SIGKILL would orphan it).
exec "${PROJECT_ROOT}/.venv/bin/python" -m orion.main_meta \
    --base-solver "${ORION_META_BASE_SOLVER}" \
    --scheduled >> "${LOG_FILE}" 2>&1
