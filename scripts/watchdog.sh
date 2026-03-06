#!/usr/bin/env bash
# Watchdog for Orion
# Checks docker compose services and restarts them if they are unhealthy or exited.
# Meant to be run as a cron job, e.g., every 5 minutes:
# */5 * * * * /path/to/Orion/scripts/watchdog.sh >> /path/to/Orion/logs/watchdog.log 2>&1

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "=== Orion Watchdog Run at $(date -u +"%Y-%m-%dT%H:%M:%SZ") ==="

# Get all services that are part of the current compose project
# Filter for exited or unhealthy
BAD_SERVICES=$(docker compose ps --format json | jq -r 'select(.State == "exited" or .Health == "unhealthy") | .Service')

if [[ -z "$BAD_SERVICES" ]]; then
  echo "All services healthy or running."
  exit 0
fi

echo "Found problematic services:"
echo "$BAD_SERVICES"

for service in $BAD_SERVICES; do
  echo "Restarting service: $service"
  docker compose restart "$service"
done

echo "Watchdog check complete."
