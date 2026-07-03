#!/usr/bin/env bash
# Wrapper for the launchd health probe — invoked once a minute by
# ~/Library/LaunchAgents/com.empire.orion.launchd-health.plist.
#
# This wrapper is intentionally minimal: any extra moving parts here
# would be a single point of failure for the system that watches all
# the OTHER single points of failure. Specifically:
#   - Uses the canonical uv path (~/.local/bin/uv) to avoid the exact
#     2026-05-22 footgun that motivated this whole probe (the
#     orphan-close plist hardcoded /opt/homebrew/bin/uv, which doesn't
#     exist on this host, so every fire silently exited 127).
#   - Sets PATH so a shell-spawned subprocess can still find launchctl
#     (which lives in /bin and /usr/bin) — launchd starts with a
#     near-empty PATH.
#   - Does NOT redirect output; launchd captures stdout/stderr per the
#     plist's StandardOutPath/StandardErrorPath. The probe writes its
#     structured alert rows to logs/launchd_health.log directly.

set -euo pipefail

PROJECT_ROOT="/Users/jacobmcmillan/Empire/Orion"
UV_BIN="${HOME}/.local/bin/uv"

export PATH="/bin:/usr/bin:/opt/homebrew/bin:/usr/local/bin:${HOME}/.local/bin:${PATH}"

# Source the gitignored .env FIRST (same discipline as run_ingestion_native.sh)
# so the probe's notifier picks up DISCORD_WEBHOOK_URL. The probe only parses
# `launchctl list`, so it needs no DB or gateway URLs of its own; sourcing .env
# is purely for the Discord alert webhook.
if [ -f "${PROJECT_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${PROJECT_ROOT}/.env"
  set +a
fi

cd "${PROJECT_ROOT}"

exec "${UV_BIN}" run python -m orion.jobs.launchd_health_probe
