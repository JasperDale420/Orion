#!/usr/bin/env bash
# Native runner for orion.main_execution — replaces the Docker container
# `orion_execution`. Runs the same Python module with the same env shape,
# but bypasses the Docker Desktop 16 GiB VM ceiling so OOMs caused by
# Heber gold growth (see predict/260513-2030-restart-loop-rca/RCA.md)
# stop happening.
#
# Connection strings target the Docker-exposed host ports for the
# services we still want containerised (TimescaleDB on 5440, Data
# Gateway on 8080). Heber data is read directly from the host cache
# directory.
#
# Lifecycle is managed by launchd via
# ~/Library/LaunchAgents/com.empire.orion.execution.plist. The KeepAlive
# directive replaces Docker's `restart: unless-stopped`; ThrottleInterval
# replaces the implicit Docker restart delay.
#
# Single-instance guard is enforced via Orion's own service-lease
# mechanism (SystemStatus row `service_lease_execution`), keyed by the
# ORION_LEASE_OWNER_ID below. The docker-compose version uses
# `orion_execution_compose`; we use `orion_execution_native` so the two
# can never co-exist without one refusing to start (Orion's lease guard
# will trip).

set -euo pipefail

PROJECT_ROOT="/Users/jacobmcmillan/Empire/Orion"
LOG_DIR="${PROJECT_ROOT}/logs"
LOG_FILE="${LOG_DIR}/execution_native.log"

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
export ORION_RUN_ID="${ORION_RUN_ID:-native_execution}"
# Distinct from the docker-compose value so the lease guard can detect
# accidental co-execution.
export ORION_LEASE_OWNER_ID="${ORION_LEASE_OWNER_ID:-orion_execution_native}"
export ORION_REQUIRE_ROLLUPS_FOR_SIGNALS_LIVE="${ORION_REQUIRE_ROLLUPS_FOR_SIGNALS_LIVE:-false}"
export ORION_RISK_MAX_DAILY_LOSS="${ORION_RISK_MAX_DAILY_LOSS:-20000}"

# Forward-testing knobs — match the docker-compose execution stanza so
# behaviour is identical to what was running in Docker.
export ORION_CIRCUIT_BREAKER_ENABLED="${ORION_CIRCUIT_BREAKER_ENABLED:-false}"
export ORION_GLOBAL_CIRCUIT_BREAKER_ENABLED="${ORION_GLOBAL_CIRCUIT_BREAKER_ENABLED:-false}"
export ORION_ML_PREFILTER_THRESHOLD="${ORION_ML_PREFILTER_THRESHOLD:-0.05}"

# Alpaca paper mode (overridden by env if operator wants live; mirrors
# the docker-compose default).
export ALPACA_PAPER="${ALPACA_PAPER:-true}"

# Logging: empire_core writes structured JSON to a daily-rotating file
# in EMPIRE_LOG_DIR; point it at the project logs dir so it sits next to
# the existing Docker service logs.
export EMPIRE_LOG_DIR="${LOG_DIR}"
export EMPIRE_LOG_FORMAT="${EMPIRE_LOG_FORMAT:-json}"

cd "${PROJECT_ROOT}"

# Use exec so launchd's process tree points directly at python; this
# makes `pgrep -f orion.main_execution` and signal forwarding work
# correctly. Stderr is folded into stdout so structured-log warnings
# and Python tracebacks end up in the same file.
exec uv run python -m orion.main_execution >> "${LOG_FILE}" 2>&1
