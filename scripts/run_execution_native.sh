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
#
# Restart safely with:
#   launchctl kickstart -k gui/$(id -u)/com.empire.orion.execution
# The final line exec's the venv python directly (NOT `uv run`) so this
# SIGKILL reaches python and leaves no orphan. If an orphaned python ever
# does linger (PPID 1, still holding the service lease), recover with:
#   launchctl bootout  gui/$(id -u)/com.empire.orion.execution
#   pkill -9 -f orion.main_execution
#   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.empire.orion.execution.plist

set -euo pipefail

PROJECT_ROOT="/Users/jacobmcmillan/Empire/Orion"
LOG_DIR="${PROJECT_ROOT}/logs"
LOG_FILE="${LOG_DIR}/execution_native.log"

mkdir -p "${LOG_DIR}"

# uv lives in ~/.local/bin (default install path); homebrew bins on Apple
# Silicon live under /opt/homebrew. launchd starts with a near-empty PATH
# so we have to set this explicitly.
export PATH="/opt/homebrew/bin:/usr/local/bin:${HOME}/.local/bin:${PATH}"

# Load operator overrides (DATA_GATEWAY_API_KEY, ORION_FLOW_SOURCE, etc.) from
# the gitignored .env FIRST, so the pinned host endpoints exported just below
# always win over any stray .env value (native must never point at docker-only
# hostnames). DATA_GATEWAY_API_KEY still reaches the GATEWAY_API_KEY expansion.
if [ -f "${PROJECT_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${PROJECT_ROOT}/.env"
  set +a
fi

# --- Service-to-service connection strings -----------------------------
# Docker-side hostnames (`timescaledb`, `data-gateway`) are unreachable
# from the host; use the host-exposed ports instead. The compose file
# maps them to localhost:5440 and localhost:8080 respectively.
export DB_URL="postgresql+asyncpg://orion:orion_password@localhost:5440/orion_db"  # pragma: allowlist secret
export GATEWAY_URL="http://localhost:8080"
# Pin the CANONICAL alias too: Orion config resolves data_gateway_url
# via AliasChoices(DATA_GATEWAY_URL, GATEWAY_URL) — DATA_GATEWAY_URL wins,
# so a stray DATA_GATEWAY_URL in .env would otherwise point native runs at
# the docker-only data-gateway host. Pinning it here (after .env sourcing)
# guarantees the host endpoint for native execution.
export DATA_GATEWAY_URL="http://localhost:8080"
# Resolve the gateway key alias-preservingly AFTER sourcing .env. Orion config
# accepts BOTH env names via AliasChoices(DATA_GATEWAY_API_KEY, GATEWAY_API_KEY),
# so an operator who set only GATEWAY_API_KEY must not have it clobbered. Prefer
# DATA_GATEWAY_API_KEY, fall back to GATEWAY_API_KEY, then fail fast — never
# substitute a hardcoded default (a revoked literal here would launch the
# service but leave it unable to auth).
_gw_key="${DATA_GATEWAY_API_KEY:-${GATEWAY_API_KEY:-}}"
if [ -z "$_gw_key" ]; then
  echo "FATAL: no gateway key (set DATA_GATEWAY_API_KEY or GATEWAY_API_KEY in .env)" >&2
  exit 78
fi
export DATA_GATEWAY_API_KEY="$_gw_key" GATEWAY_API_KEY="$_gw_key"

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
# -$2,000/day (2% of the 100k slice) halts NEW entries; exits keep running.
# The old 20k value could never trip on $500 fixed-premium trades.
export ORION_RISK_MAX_DAILY_LOSS="${ORION_RISK_MAX_DAILY_LOSS:-2000}"
export ORION_RISK_MAX_POSITIONS="${ORION_RISK_MAX_POSITIONS:-20}"
# Sample-size caps (2026-07 recovery plan): 15 option positions total,
# per-bucket 4/6/8 via ORION_RISK_OPTION_BUCKET_CAPS in config defaults,
# $500 fixed premium per trade. 0DTE entries enabled (wind-down + bucket
# exits protect the last hour).
export ORION_RISK_MAX_OPTION_POSITIONS="${ORION_RISK_MAX_OPTION_POSITIONS:-15}"
export ORION_RISK_FIXED_PREMIUM_PER_TRADE="${ORION_RISK_FIXED_PREMIUM_PER_TRADE:-500}"
export ORION_RISK_MIN_DTE="${ORION_RISK_MIN_DTE:-0}"
# Orion's allocated slice of the shared ~$1M paper account. Sizing (max
# premium/order %) computes off this, not the pooled account equity. Config
# default is also 100k; set explicitly here for operator visibility/tuning.
export ORION_RISK_ALLOCATED_EQUITY="${ORION_RISK_ALLOCATED_EQUITY:-100000}"
# Router-empty must never SKIP a candidate: the baseline solver is the
# structural fallback when no dedicated solver matches (25.5% of all skips
# on 2026-06-29/30 were "Router empty and no baseline solver applied").
export ORION_BASELINE_SOLVER_ID="${ORION_BASELINE_SOLVER_ID:-diversified_baseline_v1}"

# Forward-testing knobs — match the docker-compose execution stanza so
# behaviour is identical to what was running in Docker.
export ORION_CIRCUIT_BREAKER_ENABLED="${ORION_CIRCUIT_BREAKER_ENABLED:-false}"
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
exec "${PROJECT_ROOT}/.venv/bin/python" -m orion.main_execution >> "${LOG_FILE}" 2>&1
