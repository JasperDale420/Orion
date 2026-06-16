#!/usr/bin/env bash
# Trade attribution report — closed P&L by rule + open positions by rule.
#
# Run anytime (typically EOD) to see how each rule is performing.
# Joins fills → strategy_decisions (via underlying ticker + EXECUTE)
# → candidate_trades (for rule_id), then combines with broker positions
# to show the unrealized side too.
#
# Usage:
#   ./scripts/trade_attribution_report.sh         # full lifetime stats
#   ./scripts/trade_attribution_report.sh today   # today's slice only
#
# 2026-05-21: today's run revealed -$437K from rule_bearish_put_pressure_v1
# and -$35K from rule_bullish_sweep_v1, all on options expiring 5/22.
# Operator decision: observe one more day with all pipeline fixes in
# place before retuning/retiring rules.

set -euo pipefail

SCOPE="${1:-all}"
DATE_FILTER=""
if [[ "$SCOPE" == "today" ]]; then
  DATE_FILTER="AND created_at_utc::date = current_date"
fi

PSQL="psql -h localhost -p 5440 -U orion -d orion_db"
export PGPASSWORD=orion_password  # pragma: allowlist secret

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
if [ -f "${PROJECT_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "${PROJECT_ROOT}/.env"
  set +a
fi
_gw_key="${DATA_GATEWAY_API_KEY:-${GATEWAY_API_KEY:-}}"
if [ -z "$_gw_key" ]; then
  echo "FATAL: no gateway key (set DATA_GATEWAY_API_KEY or GATEWAY_API_KEY in .env)" >&2
  exit 78
fi

echo "============================================================"
echo "  TRADE ATTRIBUTION REPORT  ($SCOPE)"
echo "  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "============================================================"

# ── Account-level reality from Alpaca ────────────────────────────
echo
echo "── ACCOUNT (shared Alpaca paper) ──"
curl -s -H "X-Gateway-Key: ${_gw_key}" \
  "http://localhost:8080/api/v1/alpaca/account" | \
  /usr/bin/python3 -c '
import sys, json
d = json.load(sys.stdin)
p = d.get("envelope", {}).get("payload", {})
eq = float(p.get("equity", 0))
last = float(p.get("last_equity", 0))
cash = float(p.get("cash", 0))
bp = float(p.get("options_buying_power", 0))
print(f"  equity now:        ${eq:>15,.2f}")
print(f"  equity yesterday:  ${last:>15,.2f}")
pct = (eq/last - 1) * 100 if last else 0
print(f"  delta:             ${eq-last:>15,.2f}  ({pct:+.2f}%)")
print(f"  cash:              ${cash:>15,.2f}")
print(f"  options BP:        ${bp:>15,.2f}")
'

# ── Closed trades: realized P&L per rule ────────────────────────
echo
echo "── REALIZED P&L by rule (closed round-trips) ──"
$PSQL <<SQL
WITH outcomes AS (
  SELECT
    ticker AS opt_symbol,
    regexp_replace(ticker, '^([A-Z]+).*', '\1') AS underlying,
    sum(filled_qty) FILTER (WHERE side IN ('buy','BUY')) AS bot,
    sum(filled_qty) FILTER (WHERE side IN ('sell','SELL')) AS sld,
    sum(filled_qty * filled_avg_price) FILTER (WHERE side IN ('sell','SELL'))
      - sum(filled_qty * filled_avg_price) FILTER (WHERE side IN ('buy','BUY')) AS pnl
  FROM fills WHERE client_order_id LIKE 'orion_%' $DATE_FILTER
  GROUP BY ticker
)
SELECT
  ct.rule_id,
  sd.strategy_version_id AS solver,
  count(*) AS n_trades,
  sum(o.pnl)::numeric(10,2) AS total_pnl,
  avg(o.pnl)::numeric(10,2) AS avg_pnl,
  count(*) FILTER (WHERE o.pnl > 0) AS winners,
  count(*) FILTER (WHERE o.pnl < 0) AS losers
FROM outcomes o
JOIN strategy_decisions sd ON sd.ticker = o.underlying AND sd.decision = 'EXECUTE'
LEFT JOIN candidate_trades ct ON ct.candidate_id = sd.candidate_id
WHERE o.bot = o.sld AND o.bot > 0 AND o.pnl IS NOT NULL
GROUP BY ct.rule_id, sd.strategy_version_id
ORDER BY total_pnl DESC NULLS LAST;
SQL

# ── Open positions: unrealized P&L per rule via broker join ─────
echo
echo "── UNREALIZED P&L by rule (open Orion positions) ──"
curl -s -H "X-Gateway-Key: ${_gw_key}" \
  "http://localhost:8080/api/v1/alpaca/positions" | \
  /usr/bin/python3 -c '
import sys, json, re
d = json.load(sys.stdin)
positions = d.get("data", d.get("envelope", {}).get("payload", []) or [])
if isinstance(positions, dict): positions = positions.get("payload", [])
occ = re.compile(r"^([A-Z]+)\d{6}[CP]\d{8}$")
for p in positions:
    sym = p.get("symbol", "")
    m = occ.match(sym)
    if m:
        unl = m.group(1)
        mv = float(p.get("market_value", 0) or 0)
        upl = float(p.get("unrealized_pl", 0) or 0)
        print(f"{unl}|{sym}|{mv}|{upl}")
' > /tmp/__trade_report_positions.csv 2>/dev/null

$PSQL <<SQL
CREATE TEMP TABLE _open_positions (underlying TEXT, symbol TEXT, market_value FLOAT, unrealized_pl FLOAT);
\copy _open_positions FROM '/tmp/__trade_report_positions.csv' WITH (FORMAT csv, DELIMITER '|');

WITH attribution AS (
  SELECT DISTINCT
    g.symbol,
    g.market_value,
    g.unrealized_pl,
    FIRST_VALUE(ct.rule_id) OVER (PARTITION BY g.symbol ORDER BY sd.timestamp_utc DESC NULLS LAST) AS rule_id
  FROM _open_positions g
  LEFT JOIN strategy_decisions sd ON sd.ticker = g.underlying AND sd.decision = 'EXECUTE'
  LEFT JOIN candidate_trades ct ON ct.candidate_id = sd.candidate_id
)
SELECT
  COALESCE(rule_id, '(unattributed)') AS rule_id,
  count(*) AS n_positions,
  sum(market_value)::numeric(12,2) AS total_mv,
  sum(unrealized_pl)::numeric(12,2) AS total_unrealized
FROM attribution
GROUP BY rule_id
ORDER BY total_unrealized;
SQL

rm -f /tmp/__trade_report_positions.csv

# ── Decision pipeline activity ──────────────────────────────────
echo
echo "── DECISION PIPELINE health ($SCOPE) ──"
DATE_CLAUSE=""
if [[ "$SCOPE" == "today" ]]; then DATE_CLAUSE="WHERE timestamp_utc::date = current_date"; fi
$PSQL <<SQL
SELECT
  decision,
  count(*) AS n,
  count(*) FILTER (WHERE reason LIKE 'Stale at fetch%') AS stale,
  count(*) FILTER (WHERE reason LIKE 'Ensemble Rejected%') AS ensemble_rej,
  count(*) FILTER (WHERE reason LIKE 'Preflight reject%') AS preflight_rej
FROM strategy_decisions $DATE_CLAUSE
GROUP BY decision
ORDER BY n DESC;
SQL

echo
echo "============================================================"
