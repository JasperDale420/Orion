#!/usr/bin/env python3
"""
Backtest Exit Rules with P&L Calculation.

Simulates entries from UW flow signals, evaluates exit rule triggers,
and calculates P&L based on underlying price movement.
"""

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select

from orion.processing.rules.exit_rules import get_default_exit_rules
from orion.shared.db_utils import db_query
from orion.shared.logger import setup_struct_logger
from orion.storage.models_silver import SilverAlpacaBar, SilverOptionFlow

logger = setup_struct_logger("exit_backtest_pnl")


@dataclass
class SimulatedPosition:
    """Simulated position from historical flow."""

    ticker: str
    direction: str
    candidate_id: str
    entry_ts: datetime
    entry_price: float  # Underlying price at entry
    option_price: float  # Option price at entry
    option_chain: str | None = None
    entry_iv: float | None = None
    entry_premium_window: float = 0.0
    entry_sweep_count: int = 0
    entry_oi: float | None = None
    qty: float = 1.0


@dataclass
class TradeResult:
    """Result of a single simulated trade."""

    ticker: str
    entry_ts: datetime
    exit_ts: datetime
    entry_price: float
    exit_price: float
    pnl_pct: float
    pnl_usd: float
    exit_rule: str
    hold_minutes: float
    direction: str


@dataclass
class BacktestResult:
    """Aggregated backtest results with P&L."""

    total_positions: int = 0
    positions_with_exit: int = 0
    positions_no_exit: int = 0

    # P&L metrics
    total_pnl_pct: float = 0.0
    avg_pnl_pct: float = 0.0
    win_rate: float = 0.0
    winners: int = 0
    losers: int = 0
    avg_winner_pct: float = 0.0
    avg_loser_pct: float = 0.0
    max_winner_pct: float = 0.0
    max_loser_pct: float = 0.0

    # Exit metrics
    avg_hold_minutes: float = 0.0
    exit_triggers: dict[str, int] = field(default_factory=dict)
    pnl_by_rule: dict[str, list[float]] = field(default_factory=dict)

    # Individual trades
    trades: list[TradeResult] = field(default_factory=list)


async def fetch_flow_data(days: int = 7) -> list[Any]:
    """Fetch historical flow data."""

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


async def fetch_bar_data(days: int = 7) -> dict[str, list[Any]]:
    """Fetch historical 1-minute bar data for price tracking."""

    async def query(session: Any) -> list[Any]:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(SilverAlpacaBar)
            .where(SilverAlpacaBar.bar_start_ts_utc >= cutoff)
            .order_by(SilverAlpacaBar.bar_start_ts_utc.asc())
        )
        result = await session.execute(stmt)
        return result.scalars().all()

    bars = await db_query(query)

    # Group by ticker for fast lookup
    bars_by_ticker: dict[str, list[Any]] = defaultdict(list)
    for bar in bars:
        bars_by_ticker[bar.ticker].append(bar)

    return bars_by_ticker


def get_price_at_time(bars: list[Any], target_ts: datetime, tolerance_minutes: int = 5) -> float | None:
    """Get underlying price at a specific time from bar data."""
    if not bars:
        return None

    # Find closest bar
    closest_bar = None
    min_diff = timedelta(minutes=tolerance_minutes)

    for bar in bars:
        diff = abs(bar.bar_start_ts_utc - target_ts)
        if diff < min_diff:
            min_diff = diff
            closest_bar = bar

    return closest_bar.close if closest_bar else None


def identify_entries(flow_data: list[Any]) -> list[SimulatedPosition]:
    """Identify entry signals from flow data."""
    positions = []
    seen_entries: set = set()  # Prevent duplicate entries

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
            option_price = getattr(flow, "option_price", 0) or 0

            # Entry criteria: Large call sweep on ASK
            if is_sweep and aggressor == "ASK" and put_call == "C" and premium >= 50000 and underlying_price > 0:
                # Dedupe: One entry per ticker per hour
                entry_key = (ticker, flow.flow_ts_utc.strftime("%Y-%m-%d-%H"))
                if entry_key in seen_entries:
                    continue
                seen_entries.add(entry_key)

                entry_ts = flow.flow_ts_utc
                window_end = entry_ts + timedelta(minutes=5)
                sweep_count = sum(
                    1
                    for f in flows
                    if entry_ts <= f.flow_ts_utc <= window_end and str(getattr(f, "is_sweep", "")).lower() == "true"
                )

                positions.append(
                    SimulatedPosition(
                        ticker=ticker,
                        direction="LONG",
                        candidate_id=f"sim_{flow.event_id}",
                        entry_ts=entry_ts,
                        entry_price=float(underlying_price),
                        option_price=float(option_price),
                        option_chain=getattr(flow, "option_chain", None),
                        entry_iv=getattr(flow, "iv", None),
                        entry_premium_window=premium,
                        entry_sweep_count=sweep_count,
                        entry_oi=getattr(flow, "open_interest", None),
                    )
                )

    return positions


def evaluate_trade(
    position: SimulatedPosition,
    flow_data: list[Any],
    bars_by_ticker: dict[str, list[Any]],
    exit_rules: list[Any],
    max_hold_minutes: int = 120,
) -> TradeResult | None:
    """Evaluate a trade with P&L calculation using flow underlying prices."""
    # Get post-entry flow for exit evaluation
    post_entry_flow = [
        f
        for f in flow_data
        if f.ticker == position.ticker
        and f.flow_ts_utc > position.entry_ts
        and f.flow_ts_utc <= position.entry_ts + timedelta(minutes=max_hold_minutes)
    ]

    exit_ts = None
    exit_rule = None
    exit_price = None

    # Check exit rules
    for rule in exit_rules:
        signal = rule.should_exit(position, post_entry_flow, context={})
        if signal and post_entry_flow:
            # Exit at first triggering flow timestamp
            # Get price from closest post-entry flow record
            trigger_flow = min(post_entry_flow, key=lambda f: f.flow_ts_utc)
            exit_ts = trigger_flow.flow_ts_utc
            exit_price = getattr(trigger_flow, "underlying_price", 0) or 0
            exit_rule = signal.rule_id
            break

    # Default exit: max hold time - use last available price from flow
    if exit_ts is None:
        exit_ts = position.entry_ts + timedelta(minutes=max_hold_minutes)
        exit_rule = "max_hold_time"
        # Get price from last post-entry flow OR try bar data
        if post_entry_flow:
            last_flow = max(post_entry_flow, key=lambda f: f.flow_ts_utc)
            exit_price = getattr(last_flow, "underlying_price", 0) or 0
        else:
            ticker_bars = bars_by_ticker.get(position.ticker, [])
            exit_price = get_price_at_time(ticker_bars, exit_ts)

    # Validate prices
    entry_price = position.entry_price
    if not exit_price or exit_price <= 0 or entry_price <= 0:
        return None

    # Calculate P&L (for LONG: exit - entry)
    if position.direction == "LONG":
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100
        pnl_usd = (exit_price - entry_price) * 100  # Assume 100 shares
    else:
        pnl_pct = ((entry_price - exit_price) / entry_price) * 100
        pnl_usd = (entry_price - exit_price) * 100

    hold_minutes = (exit_ts - position.entry_ts).total_seconds() / 60

    return TradeResult(
        ticker=position.ticker,
        entry_ts=position.entry_ts,
        exit_ts=exit_ts,
        entry_price=entry_price,
        exit_price=exit_price,
        pnl_pct=pnl_pct,
        pnl_usd=pnl_usd,
        exit_rule=exit_rule,
        hold_minutes=hold_minutes,
        direction=position.direction,
    )


async def run_backtest(days: int = 7, max_positions: int = 100) -> BacktestResult:
    """Run backtest with P&L calculation."""
    logger.info(f"Fetching data for last {days} days...")

    flow_data = await fetch_flow_data(days)
    bars_by_ticker = await fetch_bar_data(days)

    logger.info(f"Loaded {len(flow_data)} flow records, {len(bars_by_ticker)} tickers with bars")

    if not flow_data:
        return BacktestResult()

    positions = identify_entries(flow_data)
    logger.info(f"Found {len(positions)} entry signals")

    positions = positions[:max_positions]
    exit_rules = get_default_exit_rules()

    result = BacktestResult(total_positions=len(positions))
    result.exit_triggers = defaultdict(int)
    result.pnl_by_rule = defaultdict(list)

    winners_pnl = []
    losers_pnl = []

    logger.info("Evaluating trades...")
    for pos in positions:
        trade = evaluate_trade(pos, flow_data, bars_by_ticker, exit_rules)

        if trade:
            result.trades.append(trade)
            result.exit_triggers[trade.exit_rule] += 1
            result.pnl_by_rule[trade.exit_rule].append(trade.pnl_pct)

            if trade.pnl_pct > 0:
                result.winners += 1
                winners_pnl.append(trade.pnl_pct)
            else:
                result.losers += 1
                losers_pnl.append(trade.pnl_pct)

    # Calculate aggregates
    if result.trades:
        all_pnl = [t.pnl_pct for t in result.trades]
        result.total_pnl_pct = sum(all_pnl)
        result.avg_pnl_pct = sum(all_pnl) / len(all_pnl)
        result.avg_hold_minutes = sum(t.hold_minutes for t in result.trades) / len(result.trades)
        result.positions_with_exit = len(result.trades)

    if winners_pnl:
        result.avg_winner_pct = sum(winners_pnl) / len(winners_pnl)
        result.max_winner_pct = max(winners_pnl)

    if losers_pnl:
        result.avg_loser_pct = sum(losers_pnl) / len(losers_pnl)
        result.max_loser_pct = min(losers_pnl)

    if result.winners + result.losers > 0:
        result.win_rate = result.winners / (result.winners + result.losers) * 100

    return result


def print_results(result: BacktestResult) -> None:
    """Print backtest results with P&L."""
    print("\n" + "=" * 70)
    print("EXIT RULES BACKTEST RESULTS WITH P&L")
    print("=" * 70)

    print(f"\n{'SUMMARY':^70}")
    print("-" * 70)
    print(f"Total Positions: {result.total_positions}")
    print(f"Evaluated Trades: {len(result.trades)}")

    print(f"\n{'P&L METRICS':^70}")
    print("-" * 70)
    print(f"Total P&L: {result.total_pnl_pct:+.2f}%")
    print(f"Average P&L per Trade: {result.avg_pnl_pct:+.2f}%")
    print(f"Win Rate: {result.win_rate:.1f}%")
    print(f"Winners: {result.winners} | Losers: {result.losers}")
    print(f"Avg Winner: +{result.avg_winner_pct:.2f}% | Avg Loser: {result.avg_loser_pct:.2f}%")
    print(f"Max Winner: +{result.max_winner_pct:.2f}% | Max Loser: {result.max_loser_pct:.2f}%")
    print(f"Avg Hold Time: {result.avg_hold_minutes:.1f} minutes")

    print(f"\n{'P&L BY EXIT RULE':^70}")
    print("-" * 70)
    for rule_id, pnls in sorted(result.pnl_by_rule.items(), key=lambda x: -len(x[1])):
        count = len(pnls)
        avg = sum(pnls) / len(pnls) if pnls else 0
        total = sum(pnls)
        winners = sum(1 for p in pnls if p > 0)
        win_rate = winners / count * 100 if count > 0 else 0
        print(f"  {rule_id}:")
        print(f"    Count: {count} | Total: {total:+.2f}% | Avg: {avg:+.2f}% | WinRate: {win_rate:.0f}%")

    # Top trades
    print(f"\n{'TOP 5 WINNERS':^70}")
    print("-" * 70)
    top_winners = sorted(result.trades, key=lambda t: t.pnl_pct, reverse=True)[:5]
    for t in top_winners:
        print(f"  {t.ticker}: {t.pnl_pct:+.2f}% ({t.exit_rule}) - {t.hold_minutes:.0f}min")

    print(f"\n{'TOP 5 LOSERS':^70}")
    print("-" * 70)
    top_losers = sorted(result.trades, key=lambda t: t.pnl_pct)[:5]
    for t in top_losers:
        print(f"  {t.ticker}: {t.pnl_pct:+.2f}% ({t.exit_rule}) - {t.hold_minutes:.0f}min")

    print("\n" + "=" * 70)


async def main() -> None:
    """Main entry point."""
    logger.info("Starting P&L Backtest...")
    result = await run_backtest(days=7, max_positions=100)
    print_results(result)


if __name__ == "__main__":
    asyncio.run(main())
