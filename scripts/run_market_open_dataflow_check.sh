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

# Source the gitignored .env FIRST (same discipline as run_ingestion_native.sh)
# so the check picks up SLACK_WEBHOOK_URL / DISCORD_WEBHOOK_URL and the gateway
# key. Then pin the canonical host endpoints AFTER sourcing so a stray .env
# value (e.g. a docker-only hostname) can never point the check at an
# unreachable host — the check shells out to `docker exec ... psql` for bronze
# freshness and curls the gateway on localhost:8080.
if [ -f "${PROJECT_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${PROJECT_ROOT}/.env"
  set +a
fi

export GATEWAY_URL="http://localhost:8080"
export DATA_GATEWAY_URL="http://localhost:8080"
# Expose the rotated gateway key under whichever alias .env set, preserving
# the other. Orion/the check accept BOTH names; an operator who set only
# GATEWAY_API_KEY must not have it clobbered by a default. Never substitute a
# hardcoded literal — the account rotated to a hashed key (2026-06-11), so a
# stale plaintext literal returns 401 and would false-alert "gateway down".
# FAIL FAST on a missing key (round-4 review): this check authenticates its
# gateway probe, and probing with an empty key turns an auth misconfiguration
# into a market-hours CRITICAL "Data-Gateway unreachable" page — the exact
# false-alert class the key rotation cleanup is eliminating.
_gw_key="${DATA_GATEWAY_API_KEY:-${GATEWAY_API_KEY:-}}"
if [ -z "$_gw_key" ]; then
  echo "FATAL: no gateway key (set DATA_GATEWAY_API_KEY or GATEWAY_API_KEY in .env)" >&2
  exit 78
fi
export DATA_GATEWAY_API_KEY="$_gw_key" GATEWAY_API_KEY="$_gw_key"

cd "${PROJECT_ROOT}"

exec "${UV_BIN}" run python -m orion.jobs.market_open_dataflow_check
