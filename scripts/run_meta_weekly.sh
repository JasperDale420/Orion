#!/usr/bin/env bash
# Native runner for orion.main_meta_weekly --scheduled — the weekly
# meta-search evolution loop. Replaces the never-brought-up Docker
# `meta-weekly` service (compose `scheduled` profile; see
# proposals/2026-06-10-eod-meta-diagnosis.md root-cause #2).
#
# Shape: long-running KeepAlive daemon, NOT a one-shot. main_meta_weekly.py's
# `--scheduled` mode is an internal poll loop (60s tick) that self-fires on
# Friday 17:30 ET and otherwise sleeps. The process owns its own schedule, so
# launchd's job is just to keep it alive — mirroring the ingestion/execution
# native daemons. (A StartCalendarInterval one-shot would conflict: the
# `--scheduled` loop never exits, so a calendar-fired process would just sleep
# until its own internal Friday-17:30 condition. Keeping it alive 24/7 and
# letting the loop fire is the design the code expects.)
#
# Connection strings target the Docker-exposed host port (TimescaleDB on
# 5440). The AI gateway is left to config defaults (http://localhost:8002/v1),
# the native ai-gateway endpoint.
#
# Lifecycle is managed by launchd via
# ~/Library/LaunchAgents/com.empire.orion.meta-weekly.plist.
#
# Restart safely with:
#   launchctl kickstart -k gui/$(id -u)/com.empire.orion.meta-weekly

set -euo pipefail

PROJECT_ROOT="/Users/jacobmcmillan/Empire/Orion"
LOG_DIR="${PROJECT_ROOT}/logs"
LOG_FILE="${LOG_DIR}/meta_weekly_native.log"

mkdir -p "${LOG_DIR}"

# uv lives in ~/.local/bin (default install path). launchd starts with a
# near-empty PATH so set this explicitly. NEVER reference
# /opt/homebrew/bin/uv directly (orphan-close exit-127 incident, 2026-05-22).
export PATH="/opt/homebrew/bin:/usr/local/bin:${HOME}/.local/bin:${PATH}"

# --- Service-to-service connection strings -----------------------------
export DB_URL="postgresql+asyncpg://orion:orion_password@localhost:5440/orion_db"  # pragma: allowlist secret

# Heber parquet cache on the host.
export HEBER_DATA_ROOT="${HOME}/.heber-cache/data"

# --- Orion runtime config ----------------------------------------------
export ORION_RUN_ID="${ORION_RUN_ID:-native_meta_weekly}"

# Logging.
export EMPIRE_LOG_DIR="${LOG_DIR}"
export EMPIRE_LOG_FORMAT="${EMPIRE_LOG_FORMAT:-json}"

cd "${PROJECT_ROOT}"

if ! uv sync; then
    if [[ ! -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
        echo "FATAL: uv sync failed and no interpreter at ${PROJECT_ROOT}/.venv/bin/python" >&2
        exit 1
    fi
    echo "WARN: uv sync failed; starting with existing .venv" >&2
fi

# exec the venv's python DIRECTLY — never `uv run python`.
exec "${PROJECT_ROOT}/.venv/bin/python" -m orion.main_meta_weekly \
    --scheduled >> "${LOG_FILE}" 2>&1
