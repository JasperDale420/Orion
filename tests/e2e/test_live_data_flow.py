"""
Live Data Flow Health Check: Diagnose where data stops flowing in the Orion pipeline.

Queries the real TimescaleDB for the most recent row at each pipeline stage
and reports whether data is fresh, stale, or missing entirely.

Run:
    uv run pytest tests/e2e/test_live_data_flow.py -v -s
    uv run python tests/e2e/test_live_data_flow.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

REAL_DB_URL = "postgresql+asyncpg://orion:orion_password@localhost:5440/orion_db"  # pragma: allowlist secret

# How old data can be before it's considered stale (during market hours)
STALE_THRESHOLDS = {
    "bronze_events": timedelta(minutes=5),
    "silver_signals": timedelta(minutes=5),
    "gold_ticker_rollup": timedelta(minutes=10),
    "candidate_trades": timedelta(hours=1),
    "strategy_decisions": timedelta(hours=1),
    "orders": timedelta(hours=4),
}


def _is_market_hours() -> bool:
    """Check if US equity market is currently open (rough heuristic)."""
    now = datetime.now(UTC)
    # Market hours: Mon-Fri, 13:30-20:00 UTC (9:30 AM - 4:00 PM ET)
    if now.weekday() >= 5:  # Saturday/Sunday
        return False
    return 13 <= now.hour < 20


def _age_str(dt: datetime | None) -> str:
    if dt is None:
        return "NEVER"
    age = datetime.now(UTC) - dt
    if age.total_seconds() < 60:
        return f"{age.total_seconds():.0f}s ago"
    if age.total_seconds() < 3600:
        return f"{age.total_seconds() / 60:.0f}m ago"
    if age.total_seconds() < 86400:
        return f"{age.total_seconds() / 3600:.1f}h ago"
    return f"{age.days}d ago"


async def check_data_flow() -> dict[str, dict]:
    """Query each pipeline stage and return diagnostic info."""
    from orion.storage import db

    db.configure_db(REAL_DB_URL, echo=False)

    stages: dict[str, dict] = {}
    market_open = _is_market_hours()

    async with db.async_session_factory() as session:
        # Stage 1: Bronze Events
        row = await session.execute(
            text(
                "SELECT count(*), max(event_ts_utc), "
                "count(*) FILTER (WHERE event_ts_utc > now() - interval '1 hour') as last_hour, "
                "count(*) FILTER (WHERE event_ts_utc > now() - interval '24 hours') as last_24h "
                "FROM bronze_events"
            )
        )
        r = row.one()
        stages["1_bronze_events"] = {
            "total": r[0],
            "last_ts": r[1],
            "last_hour": r[2],
            "last_24h": r[3],
        }

        # Stage 2: Silver Signals
        row = await session.execute(
            text(
                "SELECT count(*), max(signal_ts_utc), "
                "count(*) FILTER (WHERE signal_ts_utc > now() - interval '1 hour') as last_hour, "
                "count(*) FILTER (WHERE signal_ts_utc > now() - interval '24 hours') as last_24h "
                "FROM silver_signals"
            )
        )
        r = row.one()
        stages["2_silver_signals"] = {
            "total": r[0],
            "last_ts": r[1],
            "last_hour": r[2],
            "last_24h": r[3],
        }

        # Stage 3: Gold Rollups
        row = await session.execute(
            text(
                "SELECT count(*), max(timestamp_utc), "
                "count(DISTINCT ticker) as tickers, "
                "count(*) FILTER (WHERE timestamp_utc > now() - interval '1 hour') as last_hour "
                "FROM gold_ticker_rollup"
            )
        )
        r = row.one()
        stages["3_gold_rollups"] = {
            "total": r[0],
            "last_ts": r[1],
            "tickers": r[2],
            "last_hour": r[3],
        }

        # Stage 4: Candidate Trades
        row = await session.execute(
            text(
                "SELECT count(*), max(timestamp_utc), "
                "count(DISTINCT rule_id) as rules, "
                "count(*) FILTER (WHERE timestamp_utc > now() - interval '24 hours') as last_24h "
                "FROM candidate_trades"
            )
        )
        r = row.one()
        stages["4_candidates"] = {
            "total": r[0],
            "last_ts": r[1],
            "rules": r[2],
            "last_24h": r[3],
        }

        # Stage 5: Strategy Decisions
        row = await session.execute(
            text(
                "SELECT count(*), max(timestamp_utc), "
                "count(*) FILTER (WHERE decision = 'EXECUTE') as executes, "
                "count(*) FILTER (WHERE decision = 'SKIP') as skips "
                "FROM strategy_decisions"
            )
        )
        r = row.one()
        stages["5_decisions"] = {
            "total": r[0],
            "last_ts": r[1],
            "executes": r[2],
            "skips": r[3],
        }

        # Stage 6: Orders
        row = await session.execute(
            text(
                "SELECT count(*), max(created_at_utc), "
                "count(*) FILTER (WHERE client_order_id LIKE 'orion_%') as orion_orders, "
                "count(*) FILTER (WHERE status = 'accepted') as accepted "
                "FROM orders"
            )
        )
        r = row.one()
        stages["6_orders"] = {
            "total": r[0],
            "last_ts": r[1],
            "orion_orders": r[2],
            "accepted": r[3],
        }

        # Stage 7: Signals Live
        row = await session.execute(text("SELECT count(*), max(timestamp_utc) FROM signals_live"))
        r = row.one()
        stages["7_signals_live"] = {"total": r[0], "last_ts": r[1]}

        # System Status
        rows = await session.execute(text("SELECT key, status, details, last_updated_utc FROM system_status"))
        system = {}
        for r in rows.all():
            system[r[0]] = {"status": r[1], "details": r[2], "updated": r[3]}
        stages["system_status"] = system

        # Regime (latest snapshot)
        row = await session.execute(
            text(
                "SELECT trend_regime, vol_regime, risk_regime, session_regime, vix_regime, ts_utc "
                "FROM regime_snapshots ORDER BY ts_utc DESC LIMIT 1"
            )
        )
        regime_row = row.first()
        if regime_row:
            stages["regime"] = {
                "trend": regime_row[0],
                "vol": regime_row[1],
                "risk": regime_row[2],
                "session": regime_row[3],
                "vix": regime_row[4],
                "ts": regime_row[5],
            }
        else:
            stages["regime"] = None

        # Risk state
        row = await session.execute(
            text("SELECT current_equity, peak_equity, current_daily_loss, open_positions_count FROM risk_state LIMIT 1")
        )
        risk_row = row.first()
        if risk_row:
            stages["risk_state"] = {
                "equity": risk_row[0],
                "peak": risk_row[1],
                "daily_loss": risk_row[2],
                "positions": risk_row[3],
            }
        else:
            stages["risk_state"] = None

    await db.engine.dispose()
    return stages


def print_report(stages: dict[str, dict]) -> list[str]:
    """Print a human-readable diagnostic report. Returns list of issues."""
    market_open = _is_market_hours()
    issues: list[str] = []

    print(f"\n{'=' * 70}")
    print("  ORION LIVE DATA FLOW HEALTH CHECK")
    print(f"  {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Market: {'OPEN' if market_open else 'CLOSED'}")
    print(f"{'=' * 70}\n")

    # Pipeline stages
    pipeline_tables = [
        ("1_bronze_events", "Bronze Events (raw ingestion)"),
        ("2_silver_signals", "Silver Signals (normalized features)"),
        ("3_gold_rollups", "Gold Rollups (OHLCV aggregates)"),
        ("4_candidates", "Candidate Trades (rule engine output)"),
        ("5_decisions", "Strategy Decisions (signal engine)"),
        ("6_orders", "Orders (execution)"),
        ("7_signals_live", "Signals Live (live signal tracking)"),
    ]

    print("  PIPELINE STAGES")
    print(f"  {'─' * 66}")

    for key, label in pipeline_tables:
        data = stages.get(key, {})
        total = data.get("total", 0)
        last_ts = data.get("last_ts")
        age = _age_str(last_ts)

        # Determine status
        if total == 0:
            status = "EMPTY"
            marker = "!!"
        elif last_ts and market_open:
            table_name = key.split("_", 1)[1] if "_" in key else key
            threshold = STALE_THRESHOLDS.get(table_name, timedelta(hours=1))
            data_age = datetime.now(UTC) - last_ts
            if data_age > threshold:
                status = "STALE"
                marker = "!!"
            else:
                status = "FRESH"
                marker = "OK"
        elif last_ts:
            status = "HAS DATA"
            marker = "--"
        else:
            status = "EMPTY"
            marker = "!!"

        extra = ""
        if "last_hour" in data:
            extra += f"  1h={data['last_hour']}"
        if "last_24h" in data:
            extra += f"  24h={data['last_24h']}"
        if "orion_orders" in data:
            extra += f"  orion={data['orion_orders']}"
        if "executes" in data:
            extra += f"  exec={data['executes']} skip={data['skips']}"
        if "tickers" in data:
            extra += f"  tickers={data['tickers']}"
        if "rules" in data:
            extra += f"  rules={data['rules']}"

        print(f"  [{marker}] {label}")
        print(f"       total={total}  last={age}{extra}")

        if status in ("EMPTY", "STALE") and market_open:
            issues.append(f"{label}: {status}")

    # System status
    print("\n  SYSTEM STATUS")
    print(f"  {'─' * 66}")
    sys_status = stages.get("system_status", {})
    for key, info in sys_status.items():
        status_val = info.get("status", "?")
        marker = "OK" if status_val in ("HEALTHY", "CLOSED") else "!!"
        details = (info.get("details") or "")[:50]
        updated = _age_str(info.get("updated"))
        print(f"  [{marker}] {key}: {status_val}  ({updated})  {details}")
        if status_val not in ("HEALTHY", "CLOSED"):
            issues.append(f"{key}: {status_val}")

    # Regime
    print("\n  REGIME")
    print(f"  {'─' * 66}")
    regime = stages.get("regime")
    if regime:
        vol = regime.get("vol", "?")
        vix = regime.get("vix", "?")
        trading_blocked = vol in ("shock",) or vix in ("extreme",)
        marker = "!!" if trading_blocked else "OK"
        print(
            f"  [{marker}] trend={regime['trend']}  vol={vol}  risk={regime['risk']}  "
            f"session={regime['session']}  vix={vix}  ({_age_str(regime.get('ts'))})"
        )
        if trading_blocked and market_open:
            issues.append(f"Regime blocking trading: vol={vol}, vix={vix}")
    else:
        print("  [!!] No regime snapshots found")
        issues.append("No regime data")

    # Risk
    print("\n  RISK STATE")
    print(f"  {'─' * 66}")
    risk = stages.get("risk_state")
    if risk:
        equity = risk.get("equity", 0)
        peak = risk.get("peak", 0)
        dd_pct = ((peak - equity) / peak * 100) if peak > 0 else 0
        print(
            f"  [--] equity=${equity:,.2f}  peak=${peak:,.2f}  "
            f"drawdown={dd_pct:.1f}%  daily_loss=${risk.get('daily_loss', 0):,.2f}  "
            f"positions={risk.get('positions', 0)}"
        )
    else:
        print("  [--] No persisted risk state (will init from Gateway)")

    # Summary
    print(f"\n{'=' * 70}")
    if issues:
        print(f"  {len(issues)} ISSUE(S) FOUND:")
        for issue in issues:
            print(f"    - {issue}")
    else:
        print("  ALL CLEAR" + (" (market closed — data staleness not checked)" if not market_open else ""))
    print(f"{'=' * 70}\n")

    return issues


# ---------------------------------------------------------------------------
# Pytest entry point
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_live_data_flow():
    """Check live data flow health against real TimescaleDB."""
    from orion.storage import db

    stages = await check_data_flow()
    issues = print_report(stages)

    # Restore SQLite engine before conftest teardown
    db.configure_db("sqlite+aiosqlite:///:memory:", echo=False)

    # Only fail during market hours if pipeline stages are empty/stale
    if _is_market_hours() and issues:
        pytest.fail(f"Live data flow issues: {issues}")


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    stages = asyncio.run(check_data_flow())
    issues = print_report(stages)
    sys.exit(1 if issues and _is_market_hours() else 0)
