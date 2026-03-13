#!/usr/bin/env bash
# Auto-updater for Orion
# Fetches from the upstream branch, and if changes exist, pulls them.
# After pulling, it rebuilds and restarts the docker services.
# Suitable for a cron job to keep the local deployment up-to-date.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "=== Orion Auto-Updater Run at $(date -u +"%Y-%m-%dT%H:%M:%SZ") ==="

# Check if there's an upstream tracking branch configured
UPSTREAM=$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || true)
if [[ -z "$UPSTREAM" ]]; then
  echo "No upstream tracking branch configured. Cannot auto-update."
  exit 1
fi

echo "Fetching from upstream ($UPSTREAM)..."
git fetch origin

LOCAL=$(git rev-parse @)
REMOTE=$(git rev-parse "$UPSTREAM")
BASE=$(git merge-base @ "$UPSTREAM")

if [ "$LOCAL" = "$REMOTE" ]; then
    echo "Up-to-date. No action needed."
    exit 0
elif [ "$LOCAL" = "$BASE" ]; then
    echo "Need to pull new changes."
    git pull
    echo "Rebuilding and restarting docker containers..."
    docker compose up -d --build
    echo "Update complete."
elif [ "$REMOTE" = "$BASE" ]; then
    echo "Local commits ahead of upstream. Skipping auto-update to prevent conflicts."
    exit 0
else
    echo "Diverged branches! Manual intervention required."
    exit 1
fi
