"""
Exit Strategy Backtest: Compare flow-based exits vs pure price targets.

Questions to answer:
1. Would flow-based rules have exited before the 50% profit target?
2. What are the actual returns if we use flow-based rules?
3. How do different exit strategies compare?
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from orion.shared.db_utils import db_query
from orion.storage.db import init_db


async def get_labeled_entries() -> List[Dict[str, Any]]:
    """Get entries from price_target_labels that hit targets."""

    async def query(session: Any) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT
                event_id,
                ticker,
                option_chain,
                trade_type,
                entry_ts,
                entry_option_price,
                expiry,
                dte,
                premium_usd,
                aggressor,
                put_call,
                max_return_pct,
                max_drawdown_pct,
                hit_50_pct_ts,
                hit_100_pct_ts,
                hit_stop_20_pct_ts,
                first_exit_type,
                first_exit_ts,
                first_exit_return_pct
            FROM price_target_labels
            WHERE max_return_pct IS NOT NULL
            ORDER BY entry_ts ASC
        """
        )
        result = await session.execute(stmt)
        rows = result.fetchall()
        columns = result.keys()
        return [dict(zip(columns, row, strict=False)) for row in rows]

    return await db_query(query)


async def get_flows_between(ticker: str, start_ts: datetime, end_ts: datetime) -> List[Dict[str, Any]]:
    """Get flow data between two timestamps for a ticker."""

    async def query(session: Any) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT
                flow_ts_utc,
                put_call,
                aggressor,
                premium_usd,
                is_sweep,
                option_price,
                expiry
            FROM silver_uw_flow
            WHERE ticker = :ticker
            AND flow_ts_utc > :start_ts
            AND flow_ts_utc <= :end_ts
            AND premium_usd IS NOT NULL
            ORDER BY flow_ts_utc ASC
        """
        )
        result = await session.execute(
            stmt,
            {
                "ticker": ticker,
                "start_ts": start_ts,
                "end_ts": end_ts,
            },
        )
        rows = result.fetchall()
        columns = result.keys()
        return [dict(zip(columns, row, strict=False)) for row in rows]

    return await db_query(query)


def check_sentiment_reversal(
    entry: Dict[str, Any],
    flows: List[Dict[str, Any]],
    min_opposing_premium: float = 100000.0,
) -> Optional[Dict[str, Any]]:
    """Check if sentiment reversal exit would have triggered."""
    is_call_entry = entry["put_call"] == "C"

    for flow in flows:
        premium = flow.get("premium_usd") or 0
        if premium < min_opposing_premium:
            continue

        is_sweep = str(flow.get("is_sweep", "")).lower() == "true"
        if not is_sweep:
            continue

        aggressor = flow.get("aggressor", "")
        put_call = flow.get("put_call", "")

        # Opposing = bearish flow for call entry, bullish for put entry
        is_opposing = False
        if is_call_entry:
            # Bearish = ASK-side puts or BID-side calls
            if (put_call == "P" and aggressor == "ASK") or (put_call == "C" and aggressor == "BID"):
                is_opposing = True
        else:
            # Bullish = ASK-side calls or BID-side puts
            if (put_call == "C" and aggressor == "ASK") or (put_call == "P" and aggressor == "BID"):
                is_opposing = True

        if is_opposing:
            return {
                "rule": "sentiment_reversal",
                "exit_ts": flow["flow_ts_utc"],
                "exit_premium": premium,
            }

    return None


def estimate_return_at_time(
    entry: Dict[str, Any],
    exit_ts: datetime,
    flows: List[Dict[str, Any]],
) -> Optional[float]:
    """Estimate option return at a given time using nearby flow prices."""
    entry_price = entry["entry_option_price"]

    # Find closest flow for the same option chain
    closest_flow = None
    min_diff = timedelta(hours=24)

    for flow in flows:
        # Check if same option chain
        # This is tricky - we may not have exact chain match
        # Use any flow for this ticker as approximation
        flow_ts = flow["flow_ts_utc"]
        diff = abs(flow_ts - exit_ts)
        if diff < min_diff and flow.get("option_price"):
            min_diff = diff
            closest_flow = flow

    if closest_flow and closest_flow.get("option_price"):
        exit_price = closest_flow["option_price"]
        if entry_price > 0:
            return ((exit_price - entry_price) / entry_price) * 100

    return None


async def analyze_exit_strategies() -> Dict[str, Any]:
    """Main analysis: compare exit strategies."""
    await init_db()

    entries = await get_labeled_entries()
    print(f"Analyzing {len(entries)} labeled entries...")

    results = {
        "total_entries": len(entries),
        "price_target_only": {
            "winners": 0,
            "losers": 0,
            "total_return": 0.0,
            "returns": [],
        },
        "flow_based": {
            "early_exits": 0,
            "missed_targets": 0,
            "avoided_stops": 0,
            "total_return_estimate": 0.0,
            "exit_reasons": {},
        },
        "by_trade_type": {},
    }

    for entry in entries:
        trade_type = entry["trade_type"]

        if trade_type not in results["by_trade_type"]:
            results["by_trade_type"][trade_type] = {
                "total": 0,
                "price_target_wins": 0,
                "flow_early_exits": 0,
            }

        results["by_trade_type"][trade_type]["total"] += 1

        # Track price target performance
        first_exit = entry["first_exit_type"]
        first_exit_return = entry["first_exit_return_pct"]

        if first_exit and first_exit.startswith("TARGET"):
            results["price_target_only"]["winners"] += 1
            results["by_trade_type"][trade_type]["price_target_wins"] += 1
            if first_exit_return:
                results["price_target_only"]["total_return"] += first_exit_return
                results["price_target_only"]["returns"].append(first_exit_return)
        elif first_exit == "STOP_20":
            results["price_target_only"]["losers"] += 1
            if first_exit_return:
                results["price_target_only"]["total_return"] += first_exit_return
                results["price_target_only"]["returns"].append(first_exit_return)

        # Now check if flow-based rules would have triggered earlier
        entry_ts = entry["entry_ts"]
        hit_50_ts = entry.get("hit_50_pct_ts")
        hit_stop_ts = entry.get("hit_stop_20_pct_ts")

        # Define the window to check: from entry to first exit
        if hit_50_ts:
            check_end = hit_50_ts
        elif hit_stop_ts:
            check_end = hit_stop_ts
        else:
            # Use 2 hours after entry if no exit
            check_end = entry_ts + timedelta(hours=2)

        # Get flows in this window
        flows = await get_flows_between(entry["ticker"], entry_ts, check_end)

        # Check sentiment reversal
        sentiment_exit = check_sentiment_reversal(entry, flows)

        if sentiment_exit:
            results["flow_based"]["early_exits"] += 1
            results["by_trade_type"][trade_type]["flow_early_exits"] += 1

            rule = sentiment_exit["rule"]
            if rule not in results["flow_based"]["exit_reasons"]:
                results["flow_based"]["exit_reasons"][rule] = 0
            results["flow_based"]["exit_reasons"][rule] += 1

            # Did this exit miss a target?
            if hit_50_ts and sentiment_exit["exit_ts"] < hit_50_ts:
                results["flow_based"]["missed_targets"] += 1

            # Did this exit avoid a stop?
            if hit_stop_ts and sentiment_exit["exit_ts"] < hit_stop_ts:
                results["flow_based"]["avoided_stops"] += 1

    return results


def print_results(results: Dict[str, Any]) -> None:
    """Print analysis results."""
    print("\n" + "=" * 60)
    print("EXIT STRATEGY COMPARISON")
    print("=" * 60)

    total = results["total_entries"]
    pt = results["price_target_only"]
    fb = results["flow_based"]

    print(f"\nTotal entries analyzed: {total}")

    print("\n--- PURE PRICE TARGET STRATEGY ---")
    print(f"Winners (hit targets): {pt['winners']} ({100 * pt['winners'] / total:.1f}%)")
    print(f"Losers (stopped out): {pt['losers']} ({100 * pt['losers'] / total:.1f}%)")
    print(f"Total return: {pt['total_return']:.1f}%")
    if pt["returns"]:
        avg_return = sum(pt["returns"]) / len(pt["returns"])
        print(f"Average return per trade: {avg_return:.1f}%")

    print("\n--- FLOW-BASED EXIT RULES ---")
    print(f"Would have triggered early exits: {fb['early_exits']} ({100 * fb['early_exits'] / total:.1f}%)")
    print(f"  - Missed targets (exited before profit): {fb['missed_targets']}")
    print(f"  - Avoided stops (exited before loss): {fb['avoided_stops']}")
    print(f"Exit reasons: {fb['exit_reasons']}")

    print("\n--- BY TRADE TYPE ---")
    for tt, data in results["by_trade_type"].items():
        print(f"\n{tt}:")
        print(f"  Total: {data['total']}")
        print(
            f"  Price target wins: {data['price_target_wins']} ({100 * data['price_target_wins'] / data['total']:.1f}%)"
        )
        print(f"  Flow early exits: {data['flow_early_exits']} ({100 * data['flow_early_exits'] / data['total']:.1f}%)")

    # Key insight
    print("\n" + "=" * 60)
    print("KEY INSIGHT:")
    if fb["missed_targets"] > fb["avoided_stops"]:
        print("⚠️  Flow rules would REDUCE profitability - exiting too early!")
        print(f"   Missed {fb['missed_targets']} profit targets to avoid {fb['avoided_stops']} stops")
    else:
        print("✅ Flow rules would IMPROVE profitability - avoiding more stops than missing targets")
        print(f"   Avoided {fb['avoided_stops']} stops while missing {fb['missed_targets']} targets")
    print("=" * 60)


async def main():
    """Run the analysis."""
    results = await analyze_exit_strategies()
    print_results(results)


if __name__ == "__main__":
    asyncio.run(main())
