#!/usr/bin/env bash
# Native runner for orion.ingestion — replaces the Docker container
# `orion_ingestion`. Runs the same Python module with the same env shape,
# but bypasses the Docker Desktop 16 GiB VM ceiling so OOMs caused by
# Heber gold growth (the same pressure that drove the execution
# migration on 2026-05-14, and that has been driving ingestion's
# restartcount up since the May 8 recreate — see FOLLOWUPS #5) stop
# happening.
#
# Connection strings target the Docker-exposed host ports for the
# services we still want containerised (TimescaleDB on 5440, Data
# Gateway on 8080). Heber data is read directly from the host cache
# directory.
#
# Lifecycle is managed by launchd via
# ~/Library/LaunchAgents/com.empire.orion.ingestion.plist. The KeepAlive
# directive replaces Docker's `restart: unless-stopped`; ThrottleInterval
# replaces the implicit Docker restart delay.
#
# Single-instance guard is enforced via Orion's own service-lease
# mechanism (SystemStatus row `service_lease_ingestion`, populated by
# `orion.core.service_lease.acquire_service_lease("ingestion")` from
# `IngestionService.initialize`). The lease identity uses the
# `ORION_LEASE_OWNER_ID` env var below. The docker-compose stanza
# sets `orion_ingestion_compose`; we use `orion_ingestion_native` so
# the two can never co-exist without one refusing to start — whichever
# starts second raises RuntimeError and exits non-zero (visible in
# logs/ingestion_native.log + launchd's stderr file).
#
# Restart safely with:
#   launchctl kickstart -k gui/$(id -u)/com.empire.orion.ingestion
# The final line exec's the venv python directly (NOT `uv run`) so this
# SIGKILL reaches python and leaves no orphan. If an orphaned python ever
# does linger (PPID 1, still holding the service lease), recover with:
#   launchctl bootout  gui/$(id -u)/com.empire.orion.ingestion
#   pkill -9 -f orion.ingestion
#   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.empire.orion.ingestion.plist

set -euo pipefail

PROJECT_ROOT="/Users/jacobmcmillan/Empire/Orion"
LOG_DIR="${PROJECT_ROOT}/logs"
LOG_FILE="${LOG_DIR}/ingestion_native.log"

mkdir -p "${LOG_DIR}"

# uv lives in ~/.local/bin (default install path); homebrew bins on Apple
# Silicon live under /opt/homebrew. launchd starts with a near-empty PATH
# so we have to set this explicitly.
export PATH="/opt/homebrew/bin:/usr/local/bin:${HOME}/.local/bin:${PATH}"

# --- Service-to-service connection strings -----------------------------
# Docker-side hostnames (`timescaledb`, `data-gateway`) are unreachable
# from the host; use the host-exposed ports instead. The compose file
# maps them to localhost:5440 and localhost:8080 respectively.
export DB_URL="postgresql+asyncpg://orion:orion_password@localhost:5440/orion_db"  # pragma: allowlist secret
export GATEWAY_URL="http://localhost:8080"
export GATEWAY_API_KEY="${DATA_GATEWAY_API_KEY:-gw_orion_trading_key_55555}"

# Heber parquet cache on the host. Inside the container this path is
# the bind-mount target /Volumes/heber/data; natively we point at the
# actual host directory.
export HEBER_DATA_ROOT="${HOME}/.heber-cache/data"

# --- Orion runtime config ----------------------------------------------
export ORION_RUN_ID="${ORION_RUN_ID:-native_ingestion}"
# Distinct from the docker-compose value so the lease guard can detect
# accidental co-execution.
export ORION_LEASE_OWNER_ID="${ORION_LEASE_OWNER_ID:-orion_ingestion_native}"

# Alpaca paper mode (overridden by env if operator wants live; mirrors
# the docker-compose default). Ingestion may not read these directly but
# they're harmless and keep the env shape identical to the container.
export ALPACA_PAPER="${ALPACA_PAPER:-true}"

# Logging: empire_core writes structured JSON to a daily-rotating file
# in EMPIRE_LOG_DIR; point it at the project logs dir so it sits next to
# the existing Docker service logs.
export EMPIRE_LOG_DIR="${LOG_DIR}"
export EMPIRE_LOG_FORMAT="${EMPIRE_LOG_FORMAT:-json}"

cd "${PROJECT_ROOT}"

# Ensure the project virtualenv (.venv) exists and dependencies are current.
# `uv run` used to do this implicitly; we sync explicitly now because the
# exec below targets the venv interpreter directly. Tolerate a transient
# sync failure (e.g. a registry/network blip) when a usable venv already
# exists, so a hiccup can't block the restart of an already-provisioned
# service — but refuse to start if there is no interpreter at all.
if ! uv sync; then
    if [[ ! -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
        echo "FATAL: uv sync failed and no interpreter at ${PROJECT_ROOT}/.venv/bin/python" >&2
        exit 1
    fi
    echo "WARN: uv sync failed; starting with existing .venv" >&2
fi

# exec the venv's python DIRECTLY — never `uv run python`. `uv run` spawns
# python as a *child* of the uv process, so launchd ends up managing the uv
# wrapper: a SIGKILL from `launchctl kickstart -k` kills uv but leaves python
# orphaned (reparented to PID 1). The orphan keeps holding the single-instance
# service lease (SERVICE_LEASE_*, TTL SERVICE_LEASE_STALE_SECONDS=120 — see
# src/orion/core/service_lease.py), so the freshly-started instance blocks
# ~2 min waiting for the lease to go stale. exec'ing python directly makes
# launchd manage python itself, so the kill reaches python and no orphan
# survives. Stderr is folded into stdout so structured-log warnings and
# Python tracebacks end up in the same file.
exec "${PROJECT_ROOT}/.venv/bin/python" -m orion.ingestion >> "${LOG_FILE}" 2>&1
