#!/usr/bin/env python3
"""
Parameter Sweep Backtest for Exit Rules.

Tests different configurations to find optimal exit parameters:
1. Baseline (current thresholds)
2. Higher thresholds (2x premium requirements)
3. Minimum hold time (15 min cooldown before exit)
4. Confirmation requirement (2+ opposing trades)
"""

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select

from orion.shared.db_utils import db_query
from orion.shared.logger import setup_struct_logger
from orion.storage.models_silver import SilverOptionFlow

logger = setup_struct_logger("param_sweep")


@dataclass
class SimulatedPosition:
    ticker: str
    direction: str
    entry_ts: datetime
    entry_price: float
    option_chain: str | None = None
    entry_iv: float | None = None
    entry_premium_window: float = 0.0
    entry_sweep_count: int = 0
    entry_oi: float | None = None


@dataclass
class SweepResult:
    name: str
    total_trades: int
    win_rate: float
    total_pnl_pct: float
    avg_pnl_pct: float
    avg_hold_minutes: float
    exits_by_rule: dict[str, int] = field(default_factory=dict)


async def fetch_flow_data(days: int = 7) -> list[Any]:
    async def query(session: Any) -> list[Any]:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(SilverOptionFlow)
            .where(SilverOptionFlow.flow_ts_utc >= cutoff)
            .order_by(SilverOptionFlow.flow_ts_utc.asc())
        )
        result = await session.execute(stmt)
        return result.scalars().all()

    return await db_query(query)


def identify_entries(flow_data: list[Any]) -> list[SimulatedPosition]:
    positions = []
    seen = set()
    flow_by_ticker: dict[str, list[Any]] = defaultdict(list)

    for flow in flow_data:
        flow_by_ticker[flow.ticker].append(flow)

    for ticker, flows in flow_by_ticker.items():
        for flow in flows:
            is_sweep = str(getattr(flow, "is_sweep", "")).lower() == "true"
            aggressor = getattr(flow, "aggressor", "") or ""
            premium = getattr(flow, "premium_usd", 0) or 0
            put_call = getattr(flow, "put_call", "") or ""
            underlying_price = getattr(flow, "underlying_price", 0) or 0

            if is_sweep and aggressor == "ASK" and put_call == "C" and premium >= 50000 and underlying_price > 0:
                key = (ticker, flow.flow_ts_utc.strftime("%Y-%m-%d-%H"))
                if key in seen:
                    continue
                seen.add(key)

                positions.append(
                    SimulatedPosition(
                        ticker=ticker,
                        direction="LONG",
                        entry_ts=flow.flow_ts_utc,
                        entry_price=float(underlying_price),
                        entry_iv=getattr(flow, "iv", None),
                        entry_premium_window=premium,
                    )
                )

    return positions


def evaluate_with_config(
    position: SimulatedPosition,
    flow_data: list[Any],
    config: dict[str, Any],
    max_hold_minutes: int = 120,
) -> dict[str, Any] | None:
    """Evaluate trade with configurable exit parameters."""
    min_hold_minutes = config.get("min_hold_minutes", 0)
    min_premium = config.get("min_premium", 100000)
    require_confirmation = config.get("require_confirmation", 1)
    require_price_move = config.get("require_price_move_pct", 0)

    # Get post-entry flow
    earliest_exit = position.entry_ts + timedelta(minutes=min_hold_minutes)
    post_entry_flow = [
        f
        for f in flow_data
        if f.ticker == position.ticker
        and f.flow_ts_utc > position.entry_ts
        and f.flow_ts_utc <= position.entry_ts + timedelta(minutes=max_hold_minutes)
    ]

    if not post_entry_flow:
        return None

    exit_ts = None
    exit_price = None
    exit_rule = None

    # Check sentiment reversal with config
    is_long = position.direction == "LONG"
    opposing_count = 0
    first_opposing_flow = None

    for flow in sorted(post_entry_flow, key=lambda f: f.flow_ts_utc):
        # Skip if before minimum hold time
        if flow.flow_ts_utc < earliest_exit:
            continue

        premium = getattr(flow, "premium_usd", 0) or 0
        aggressor = getattr(flow, "aggressor", "") or ""
        put_call = getattr(flow, "put_call", "") or ""
        is_sweep = str(getattr(flow, "is_sweep", "")).lower() == "true"
        underlying = getattr(flow, "underlying_price", 0) or 0

        # Check price move requirement
        if require_price_move > 0 and position.entry_price > 0:
            price_move_pct = ((underlying - position.entry_price) / position.entry_price) * 100
            if is_long and price_move_pct > -require_price_move:
                continue  # Not enough adverse move

        # Check opposing flow
        is_opposing = False
        if is_long:
            if (put_call == "P" and aggressor == "ASK") or (put_call == "C" and aggressor == "BID"):
                is_opposing = True
        else:
            if (put_call == "C" and aggressor == "ASK") or (put_call == "P" and aggressor == "BID"):
                is_opposing = True

        if is_opposing and is_sweep and premium >= min_premium:
            opposing_count += 1
            if first_opposing_flow is None:
                first_opposing_flow = flow

            if opposing_count >= require_confirmation:
                exit_ts = flow.flow_ts_utc
                exit_price = underlying
                exit_rule = "sentiment_reversal"
                break

    # If no exit triggered, check max hold
    if exit_ts is None:
        exit_ts = position.entry_ts + timedelta(minutes=max_hold_minutes)
        exit_rule = "max_hold"
        if post_entry_flow:
            last = max(post_entry_flow, key=lambda f: f.flow_ts_utc)
            exit_price = getattr(last, "underlying_price", 0) or 0

    if not exit_price or exit_price <= 0 or position.entry_price <= 0:
        return None

    pnl_pct = ((exit_price - position.entry_price) / position.entry_price) * 100
    hold_minutes = (exit_ts - position.entry_ts).total_seconds() / 60

    return {
        "pnl_pct": pnl_pct,
        "hold_minutes": hold_minutes,
        "exit_rule": exit_rule,
        "winner": pnl_pct > 0,
    }


def run_sweep(
    positions: list[SimulatedPosition], flow_data: list[Any], config: dict[str, Any], name: str
) -> SweepResult:
    """Run backtest with specific config."""
    trades = []
    exits_by_rule: dict[str, int] = defaultdict(int)

    for pos in positions[:100]:
        result = evaluate_with_config(pos, flow_data, config)
        if result:
            trades.append(result)
            exits_by_rule[result["exit_rule"]] += 1

    if not trades:
        return SweepResult(name=name, total_trades=0, win_rate=0, total_pnl_pct=0, avg_pnl_pct=0, avg_hold_minutes=0)

    winners = sum(1 for t in trades if t["winner"])
    total_pnl = sum(t["pnl_pct"] for t in trades)
    avg_pnl = total_pnl / len(trades)
    avg_hold = sum(t["hold_minutes"] for t in trades) / len(trades)
    win_rate = winners / len(trades) * 100

    return SweepResult(
        name=name,
        total_trades=len(trades),
        win_rate=win_rate,
        total_pnl_pct=total_pnl,
        avg_pnl_pct=avg_pnl,
        avg_hold_minutes=avg_hold,
        exits_by_rule=dict(exits_by_rule),
    )


async def main() -> None:
    logger.info("Fetching data...")
    flow_data = await fetch_flow_data(days=7)
    logger.info(f"Loaded {len(flow_data)} flow records")

    positions = identify_entries(flow_data)
    logger.info(f"Found {len(positions)} entries")

    # Define test configurations
    configs = [
        ("BASELINE", {"min_premium": 100000, "min_hold_minutes": 0, "require_confirmation": 1}),
        ("HIGHER_THRESHOLD", {"min_premium": 250000, "min_hold_minutes": 0, "require_confirmation": 1}),
        ("15_MIN_COOLDOWN", {"min_premium": 100000, "min_hold_minutes": 15, "require_confirmation": 1}),
        ("30_MIN_COOLDOWN", {"min_premium": 100000, "min_hold_minutes": 30, "require_confirmation": 1}),
        ("CONFIRM_2X", {"min_premium": 100000, "min_hold_minutes": 0, "require_confirmation": 2}),
        ("CONFIRM_3X", {"min_premium": 100000, "min_hold_minutes": 0, "require_confirmation": 3}),
        (
            "PRICE_FILTER_0.5%",
            {"min_premium": 100000, "min_hold_minutes": 0, "require_confirmation": 1, "require_price_move_pct": 0.5},
        ),
        ("COMBINED_BEST", {"min_premium": 200000, "min_hold_minutes": 15, "require_confirmation": 2}),
    ]

    print("\n" + "=" * 90)
    print("PARAMETER SWEEP RESULTS")
    print("=" * 90)
    print(f"\n{'Config':<20} {'Trades':>7} {'Win%':>7} {'Total P&L':>12} {'Avg P&L':>10} {'Avg Hold':>10}")
    print("-" * 90)

    for name, config in configs:
        result = run_sweep(positions, flow_data, config, name)
        print(
            f"{result.name:<20} {result.total_trades:>7} {result.win_rate:>6.1f}% "
            f"{result.total_pnl_pct:>+11.2f}% {result.avg_pnl_pct:>+9.2f}% {result.avg_hold_minutes:>9.1f}m"
        )

    print("\n" + "=" * 90)
    print("\nLEGEND:")
    print("  BASELINE: Current thresholds ($100K premium, no cooldown)")
    print("  HIGHER_THRESHOLD: Require $250K+ opposing premium")
    print("  15/30_MIN_COOLDOWN: Don't exit before N minutes")
    print("  CONFIRM_2X/3X: Require N opposing sweeps to trigger")
    print("  PRICE_FILTER: Only exit if price moved 0.5% against position")
    print("  COMBINED_BEST: Mix of threshold + cooldown + confirmation")


if __name__ == "__main__":
    asyncio.run(main())
