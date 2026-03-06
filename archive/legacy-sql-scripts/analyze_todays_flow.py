#!/usr/bin/env python3
"""
Analyze today's flow data through ML models.
See what trades would have been taken and their P&L.
"""

import asyncio
from datetime import UTC, datetime

from orion.ml.flow_enricher import enrich_flow_for_scoring
from orion.ml.scorer import MLScorer, get_trade_bucket
from orion.shared.db_utils import db_query
from sqlalchemy import text


async def analyze_todays_flow():
    # Get today's or most recent trading day's high-conviction flow
    from datetime import timedelta

    today = datetime.now(UTC).date()
    # Use yesterday if checking after hours or if today has no flow
    check_date = today - timedelta(days=1)  # Jan 9

    async def get_flows(session):
        stmt = text("""
            SELECT
                f.event_id, f.ticker, f.put_call, f.option_chain, f.expiry,
                f.flow_ts_utc, f.premium_usd, f.is_sweep, f.aggressor
            FROM silver_uw_flow f
            WHERE f.flow_ts_utc::date = :today
            AND f.premium_usd >= 50000
            AND f.aggressor IN ('ASK', 'BID')
            ORDER BY f.premium_usd DESC
            LIMIT 100
        """)
        result = await session.execute(stmt, {"today": check_date})
        return result.mappings().all()

    flows = await db_query(get_flows)
    print(f"\n=== Flow Analysis ({check_date}) ===")
    print(f"Found {len(flows)} qualifying flows (>= $50k)")

    if not flows:
        print("No qualifying flow today")
        return

    # Initialize scorer
    scorer = MLScorer()

    # Score each flow
    scored_flows = []
    for f in flows:
        try:
            # Enrich with features
            enriched = await enrich_flow_for_scoring(
                ticker=f["ticker"],
                entry_ts=f["flow_ts_utc"],
                put_call=f["put_call"],
                premium_usd=f["premium_usd"],
                event_id=f["event_id"],
                option_chain=f["option_chain"],
                aggressor=f["aggressor"],
                is_sweep=f["is_sweep"] == "true",
                expiry=f["expiry"],  # Pass expiry for DTE calculation
            )

            # Score it - scorer.score returns a float
            score = scorer.score(enriched)

            # Collect all, not just > 0.5
            scored_flows.append(
                {
                    "ticker": f["ticker"],
                    "put_call": f["put_call"],
                    "premium": f["premium_usd"],
                    "is_sweep": f["is_sweep"] == "true",
                    "aggressor": f["aggressor"],
                    "score": score,
                    "bucket": get_trade_bucket(enriched.get("dte")),
                    "event_id": f["event_id"],
                    "flow_ts": f["flow_ts_utc"],
                    "option_chain": f["option_chain"],
                }
            )
        except Exception as e:
            print(f"Error scoring {f['ticker']}: {e}")

    # Sort by score
    scored_flows.sort(key=lambda x: x["score"], reverse=True)

    print("\n=== ML-Scored Trades (All Scores) ===")
    print(f"Found {len(scored_flows)} tradeable signals")
    print("-" * 90)
    print(f"{'Ticker':<8} {'P/C':<4} {'Premium':>12} {'Sweep':>6} {'Aggressor':>10} {'Score':>8} {'Bucket':>12}")
    print("-" * 90)

    for f in scored_flows[:20]:
        sweep = "Yes" if f["is_sweep"] else "No"
        print(
            f"{f['ticker']:<8} {f['put_call']:<4} ${f['premium']:>10,.0f} {sweep:>6} {f['aggressor']:>10} {f['score']:>7.2%} {f['bucket']:>12}"
        )

    # Now check actual outcomes from price_target_labels
    if scored_flows:
        event_ids = [f["event_id"] for f in scored_flows[:20]]

        async def get_outcomes(session):
            stmt = text("""
                SELECT
                    event_id, max_return_pct, max_drawdown_pct,
                    return_at_1h, return_at_2h, return_at_4h,
                    first_exit_type, first_exit_return_pct
                FROM price_target_labels
                WHERE event_id = ANY(:event_ids)
            """)
            result = await session.execute(stmt, {"event_ids": event_ids})
            return {r["event_id"]: dict(r) for r in result.mappings().all()}

        outcomes = await db_query(get_outcomes)

        if outcomes:
            print("\n=== Actual Outcomes (from labels) ===")
            print("-" * 90)
            print(f"{'Ticker':<8} {'Score':>8} {'MaxRet':>10} {'MaxDD':>10} {'1h Ret':>10} {'Exit Type':>12}")
            print("-" * 90)

            total_pnl = 0
            winners = 0

            for f in scored_flows[:20]:
                outcome = outcomes.get(f["event_id"])
                if outcome:
                    max_ret = outcome.get("max_return_pct") or 0
                    max_dd = outcome.get("max_drawdown_pct") or 0
                    ret_1h = outcome.get("return_at_1h") or 0
                    exit_type = outcome.get("first_exit_type") or "N/A"
                    exit_ret = outcome.get("first_exit_return_pct") or 0

                    print(
                        f"{f['ticker']:<8} {f['score']:>7.2%} {max_ret:>9.1f}% {max_dd:>9.1f}% {ret_1h:>9.1f}% {exit_type:>12}"
                    )

                    # Simple P&L calc - assume we exit at 1h or first exit
                    pnl = exit_ret if exit_ret else ret_1h
                    total_pnl += pnl
                    if pnl > 0:
                        winners += 1

            print("-" * 90)
            if outcomes:
                win_rate = winners / len(outcomes) * 100 if outcomes else 0
                avg_pnl = total_pnl / len(outcomes) if outcomes else 0
                print(f"Trades with outcomes: {len(outcomes)}")
                print(f"Winners: {winners} ({win_rate:.0f}%)")
                print(f"Average P&L: {avg_pnl:.1f}%")
                print(f"Total P&L: {total_pnl:.1f}%")
        else:
            print("\nNo labeled outcomes yet for today's trades")
            print("(Labels are generated after price tracking completes)")


if __name__ == "__main__":
    asyncio.run(analyze_todays_flow())
