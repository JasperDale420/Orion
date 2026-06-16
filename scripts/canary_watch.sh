#!/usr/bin/env bash
# Canary watcher — observe the FIRST orion-prefixed entry order to fully
# fill at the broker after baseline_count, then immediately bootout the
# execution daemon to halt further entries.
#
# Validates the live ExecutionEngine.execute_signal path end-to-end:
# preflight → two-phase persist → Gateway POST → broker accept → fill
# webhook → orders.status='filled'. Codex review 2026-05-26 caught
# two bugs in the v1 of this script that this version fixes:
#
#  1) v1 detected on ANY new `orders` row, including PENDING_SUBMIT
#     rows that two-phase persistence writes BEFORE the Gateway call
#     (execution_engine.py:725-744). Booting out the daemon at PENDING
#     would interrupt the broker/finalize window and create the exact
#     orphan condition we just fixed. v2 waits for status='filled'.
#
#  2) v1's post-halt diagnostic SELECT'd `symbol` from `orders`, but
#     the column is `ticker`. v2 corrects the column.
#
# Also: v1 counted exits + brackets + rejected orders. v2 narrows to
# BUY orders only (Orion is options-only and only opens via BUY) so
# the canary doesn't fire on an exit that happens to land first.
#
# Usage: ./scripts/canary_watch.sh <baseline_filled_count> [max_wait_sec]
set -euo pipefail

BASELINE="${1:?baseline filled-buy order count required}"
MAX_WAIT="${2:-1800}"  # 30 min default

export PGPASSWORD=orion_password  # pragma: allowlist secret
PSQL="psql -h localhost -p 5440 -U orion -d orion_db -tA -c"

echo "[$(date -u +%H:%M:%S)Z] canary armed: baseline_filled=$BASELINE, max_wait=${MAX_WAIT}s"

START=$(date +%s)
LAST_LOGGED_COUNT="$BASELINE"

# Filter scope: orion-prefixed (not orion_orphan_close_), BUY side
# only (entries; never an exit), and status='filled' (broker accept
# confirmed; not PENDING_SUBMIT, not REJECTED).
FILTER="created_at_utc::date = current_date \
  AND client_order_id LIKE 'orion_%' \
  AND client_order_id NOT LIKE 'orion_orphan_close_%' \
  AND lower(side) = 'buy' \
  AND lower(status) = 'filled'"

while true; do
  NOW=$(date +%s)
  ELAPSED=$((NOW - START))
  if (( ELAPSED > MAX_WAIT )); then
    echo "[$(date -u +%H:%M:%S)Z] timeout: no filled entry in ${MAX_WAIT}s. daemon left running."
    exit 2
  fi

  CURRENT=$($PSQL "SELECT count(*) FROM orders WHERE $FILTER;")

  if [[ "$CURRENT" -gt "$BASELINE" ]]; then
    NEW=$((CURRENT - BASELINE))
    echo "[$(date -u +%H:%M:%S)Z] CANARY FIRED — $NEW new FILLED entry order(s) detected. Halting execution daemon."
    launchctl bootout gui/$(id -u)/com.empire.orion.execution 2>&1 || echo "(bootout returned non-zero)"
    echo "[$(date -u +%H:%M:%S)Z] daemon halted. Newly-filled orion entries:"
    $PSQL "SELECT client_order_id, ticker, side, qty, limit_price, status, broker_order_id, created_at_utc FROM orders WHERE $FILTER ORDER BY created_at_utc DESC LIMIT $((NEW + 2)) ;"
    exit 0
  fi

  if (( ELAPSED % 60 == 0 )) && [[ "$CURRENT" != "$LAST_LOGGED_COUNT" ]]; then
    echo "[$(date -u +%H:%M:%S)Z] still watching... filled_buys=$CURRENT (baseline=$BASELINE)"
    LAST_LOGGED_COUNT="$CURRENT"
  fi

  sleep 3
done
