#!/usr/bin/env bash
# Canary watcher — observe the FIRST new orion-prefixed order to land after
# baseline_count, then immediately bootout the execution daemon to halt
# further entries.
#
# Validates the live ExecutionEngine.execute_signal path end-to-end after
# today's Gateway-client fix (commit 51699c8). The orphan-close already
# proved the broker leg works; this canary proves the persistence leg.
#
# Usage: ./scripts/canary_watch.sh <baseline_order_count> [max_wait_sec]
set -euo pipefail

BASELINE="${1:?baseline order count required}"
MAX_WAIT="${2:-1800}"  # 30 min default

export PGPASSWORD=orion_password  # pragma: allowlist secret
PSQL="psql -h localhost -p 5440 -U orion -d orion_db -tA -c"

echo "[$(date -u +%H:%M:%S)Z] canary armed: baseline=$BASELINE, max_wait=${MAX_WAIT}s"

START=$(date +%s)
LAST_LOGGED_COUNT="$BASELINE"

while true; do
  NOW=$(date +%s)
  ELAPSED=$((NOW - START))
  if (( ELAPSED > MAX_WAIT )); then
    echo "[$(date -u +%H:%M:%S)Z] timeout: no new entry in ${MAX_WAIT}s. daemon left running."
    exit 2
  fi

  CURRENT=$($PSQL "SELECT count(*) FROM orders WHERE created_at_utc::date = current_date AND client_order_id LIKE 'orion_%' AND client_order_id NOT LIKE 'orion_orphan_close_%';")

  if [[ "$CURRENT" -gt "$BASELINE" ]]; then
    NEW=$((CURRENT - BASELINE))
    echo "[$(date -u +%H:%M:%S)Z] CANARY FIRED — $NEW new order(s) detected. Halting execution daemon."
    launchctl bootout gui/$(id -u)/com.empire.orion.execution 2>&1 || echo "(bootout returned non-zero)"
    echo "[$(date -u +%H:%M:%S)Z] daemon halted. Latest orion entry orders:"
    $PSQL "SELECT client_order_id, symbol, side, qty, limit_price, status, created_at_utc FROM orders WHERE created_at_utc::date = current_date AND client_order_id LIKE 'orion_%' AND client_order_id NOT LIKE 'orion_orphan_close_%' ORDER BY created_at_utc DESC LIMIT $((NEW + 2)) ;"
    exit 0
  fi

  if (( ELAPSED % 60 == 0 )) && [[ "$CURRENT" != "$LAST_LOGGED_COUNT" ]]; then
    echo "[$(date -u +%H:%M:%S)Z] still watching... orders=$CURRENT (baseline=$BASELINE)"
    LAST_LOGGED_COUNT="$CURRENT"
  fi

  sleep 3
done
