#!/usr/bin/env bash
# Sync Heber silver/gold data from external drive to local SSD cache
# for Docker Desktop access (USB volumes can't be bind-mounted).
#
# Usage: Run manually or via cron before market open.
#   ./scripts/sync-heber-cache.sh

set -euo pipefail

SRC="${HEBER_VOLUME_ROOT:-/Volumes/heber}/data"
DST="${HEBER_HOST_DATA:-/Users/jacobmcmillan/.heber-cache/data}"

if [ ! -d "$SRC" ]; then
    echo "ERROR: Heber source not found at $SRC (is the external drive mounted?)"
    exit 1
fi

mkdir -p "$DST/silver" "$DST/gold"

FEEDS="flow_alerts bars darkpool market_tide greek_exposure iv_rank max_pain quotes trades"

for feed in $FEEDS; do
    if [ -d "$SRC/silver/feed=$feed" ]; then
        rsync -a --delete "$SRC/silver/feed=$feed/" "$DST/silver/feed=$feed/"
        echo "Synced silver/feed=$feed"
    fi
done

if [ -d "$SRC/gold" ]; then
    rsync -a --delete "$SRC/gold/" "$DST/gold/"
    echo "Synced gold"
fi

echo "Heber cache sync complete: $(du -sh "$DST" | cut -f1)"
