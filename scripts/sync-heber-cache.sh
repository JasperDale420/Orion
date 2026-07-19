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

SYNCED=0
for feed in $FEEDS; do
    if [ -d "$SRC/silver/feed=$feed" ]; then
        rsync -a --delete "$SRC/silver/feed=$feed/" "$DST/silver/feed=$feed/"
        echo "Synced silver/feed=$feed"
        SYNCED=$((SYNCED + 1))
    else
        echo "WARNING: silver/feed=$feed missing under $SRC — skipped" >&2
    fi
done

if [ -d "$SRC/gold" ]; then
    rsync -a --delete "$SRC/gold/" "$DST/gold/"
    echo "Synced gold"
    SYNCED=$((SYNCED + 1))
else
    echo "WARNING: gold layer missing under $SRC — skipped" >&2
fi

# A run that synced nothing means the mount is broken/empty (a zombie mount can
# pass the top-level -d check); Orion would silently serve a stale cache.
if [ "$SYNCED" -eq 0 ]; then
    echo "ERROR: nothing synced from $SRC — mount broken or empty; cache left stale" >&2
    exit 1
fi

echo "Heber cache sync complete: $(du -sh "$DST" | cut -f1)"
