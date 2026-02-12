#!/usr/bin/env python3
"""
Backfill ML Features for price_target_labels.

Standalone script that updates existing records with new ML features.
Uses simple, isolated queries to avoid transaction cascade issues.

Usage:
    docker-compose run --rm price_target_labeler python scripts/backfill_ml_features.py --batch-size 100
"""

import argparse
import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from orion.shared.db_utils import db_query, db_write
from orion.storage.db import init_db


async def get_records_to_backfill(batch_size: int) -> List[Dict[str, Any]]:
    """Get records with missing features."""

    async def query(session: Any) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT event_id, ticker, option_chain, entry_ts, expiry, dte, iv_at_entry
            FROM price_target_labels
            WHERE rvol_1h IS NULL
               OR spy_return_1h IS NULL
               OR high_52w_distance_pct IS NULL
            ORDER BY entry_ts DESC
            LIMIT :batch_size
        """
        )
        result = await session.execute(stmt, {"batch_size": batch_size})
        rows = result.fetchall()
        return [
            {
                "event_id": r[0],
                "ticker": r[1],
                "option_chain": r[2],
                "entry_ts": r[3],
                "expiry": r[4],
                "dte": r[5] or 0,
                "iv_at_entry": r[6],
            }
            for r in rows
        ]

    return await db_query(query)


async def calculate_rvol_1h(ticker: str, entry_ts: datetime) -> Optional[float]:
    """Calculate 1-hour relative volume."""

    async def query(session: Any) -> Optional[float]:
        try:
            lookback_1h = entry_ts - timedelta(hours=1)
            lookback_30d = entry_ts - timedelta(days=30)

            # Current hour volume
            current_stmt = text(
                """
                SELECT SUM(volume) FROM silver_alpaca_bars
                WHERE ticker = :ticker
                AND bar_start_ts_utc >= :lookback_1h
                AND bar_start_ts_utc < :entry_ts
            """
            )
            current_result = await session.execute(
                current_stmt, {"ticker": ticker, "lookback_1h": lookback_1h, "entry_ts": entry_ts}
            )
            current_vol = current_result.scalar() or 0

            # Average hourly volume over 30 days
            avg_stmt = text(
                """
                SELECT AVG(hourly_vol) FROM (
                    SELECT DATE_TRUNC('hour', bar_start_ts_utc) as hour, SUM(volume) as hourly_vol
                    FROM silver_alpaca_bars
                    WHERE ticker = :ticker
                    AND bar_start_ts_utc >= :lookback_30d
                    AND bar_start_ts_utc < :lookback_1h
                    GROUP BY DATE_TRUNC('hour', bar_start_ts_utc)
                ) t
            """
            )
            avg_result = await session.execute(
                avg_stmt, {"ticker": ticker, "lookback_30d": lookback_30d, "lookback_1h": lookback_1h}
            )
            avg_vol = avg_result.scalar() or 0

            if avg_vol > 0:
                return float(current_vol / avg_vol)
        except Exception:
            pass
        return None

    return await db_query(query)


async def calculate_spy_return_1h(entry_ts: datetime) -> Optional[float]:
    """Calculate SPY return in last hour."""

    async def query(session: Any) -> Optional[float]:
        try:
            lookback_1h = entry_ts - timedelta(hours=1)

            stmt = text(
                """
                SELECT
                    (SELECT close FROM silver_alpaca_bars WHERE ticker = 'SPY' AND bar_start_ts_utc < :entry_ts ORDER BY bar_start_ts_utc DESC LIMIT 1),
                    (SELECT close FROM silver_alpaca_bars WHERE ticker = 'SPY' AND bar_start_ts_utc < :lookback_1h ORDER BY bar_start_ts_utc DESC LIMIT 1)
            """
            )
            result = await session.execute(stmt, {"entry_ts": entry_ts, "lookback_1h": lookback_1h})
            row = result.fetchone()

            if row and row[0] and row[1] and row[1] > 0:
                return ((row[0] - row[1]) / row[1]) * 100
        except Exception:
            pass
        return None

    return await db_query(query)


async def calculate_52w_high_distance(ticker: str, entry_ts: datetime) -> Optional[float]:
    """Calculate % below 52-week high."""

    async def query(session: Any) -> Optional[float]:
        try:
            entry_date = entry_ts.date()
            lookback_52w = entry_date - timedelta(days=365)

            high_stmt = text(
                """
                SELECT MAX(high) FROM silver_alpaca_bars
                WHERE ticker = :ticker
                AND DATE(bar_start_ts_utc) >= :lookback_52w
                AND DATE(bar_start_ts_utc) < :entry_date
            """
            )
            high_result = await session.execute(
                high_stmt, {"ticker": ticker, "lookback_52w": lookback_52w, "entry_date": entry_date}
            )
            high_52w = high_result.scalar()

            price_stmt = text(
                """
                SELECT close FROM silver_alpaca_bars
                WHERE ticker = :ticker AND bar_start_ts_utc < :entry_ts
                ORDER BY bar_start_ts_utc DESC LIMIT 1
            """
            )
            price_result = await session.execute(price_stmt, {"ticker": ticker, "entry_ts": entry_ts})
            current_price = price_result.scalar()

            if high_52w and current_price and high_52w > 0:
                return ((high_52w - current_price) / high_52w) * 100
        except Exception:
            pass
        return None

    return await db_query(query)


async def calculate_darkpool_1h(ticker: str, entry_ts: datetime) -> Optional[float]:
    """Calculate 1-hour darkpool volume."""

    async def query(session: Any) -> Optional[float]:
        try:
            lookback_1h = entry_ts - timedelta(hours=1)
            stmt = text(
                """
                SELECT SUM(volume) FROM silver_uw_darkpool
                WHERE ticker = :ticker
                AND executed_at_utc >= :lookback_1h
                AND executed_at_utc < :entry_ts
            """
            )
            result = await session.execute(stmt, {"ticker": ticker, "lookback_1h": lookback_1h, "entry_ts": entry_ts})
            return result.scalar()
        except Exception:
            pass
        return None

    return await db_query(query)


def calculate_minutes_to_close(entry_ts: datetime) -> Optional[int]:
    """Calculate minutes to market close (4pm ET)."""
    try:
        et_offset = timedelta(hours=-5)
        et_time = entry_ts + et_offset
        close_time = et_time.replace(hour=16, minute=0, second=0, microsecond=0)

        if et_time < close_time:
            diff = (close_time - et_time).total_seconds() / 60
            return int(diff)
    except Exception:
        pass
    return None


async def update_record(record: Dict[str, Any]) -> bool:
    """Update a single record with calculated features."""
    try:
        ticker = record["ticker"]
        entry_ts = record["entry_ts"]
        event_id = record["event_id"]

        # Calculate features (each in separate transaction)
        rvol_1h = await calculate_rvol_1h(ticker, entry_ts)
        spy_return_1h = await calculate_spy_return_1h(entry_ts)
        high_52w_dist = await calculate_52w_high_distance(ticker, entry_ts)
        darkpool_1h = await calculate_darkpool_1h(ticker, entry_ts)
        minutes_to_close = calculate_minutes_to_close(entry_ts)

        # Update record
        async def do_update(session: Any) -> None:
            update_stmt = text(
                """
                UPDATE price_target_labels SET
                    rvol_1h = COALESCE(rvol_1h, :rvol_1h),
                    spy_return_1h = COALESCE(spy_return_1h, :spy_return_1h),
                    high_52w_distance_pct = COALESCE(high_52w_distance_pct, :high_52w_dist),
                    darkpool_volume_1h = COALESCE(darkpool_volume_1h, :darkpool_1h),
                    minutes_to_close = COALESCE(minutes_to_close, :minutes_to_close)
                WHERE event_id = :event_id
            """
            )
            await session.execute(
                update_stmt,
                {
                    "event_id": event_id,
                    "rvol_1h": rvol_1h,
                    "spy_return_1h": spy_return_1h,
                    "high_52w_dist": high_52w_dist,
                    "darkpool_1h": darkpool_1h,
                    "minutes_to_close": minutes_to_close,
                },
            )

        await db_write(do_update)
        return True
    except Exception as e:
        print(f"Error updating {record['event_id']}: {e}")
        return False


async def main():
    parser = argparse.ArgumentParser(description="Backfill ML features")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size")
    parser.add_argument("--max-records", type=int, default=0, help="Max records (0=unlimited)")
    args = parser.parse_args()

    # Initialize database
    await init_db()

    print(f"Starting backfill with batch_size={args.batch_size}")
    total_updated = 0
    total_failed = 0

    records = await get_records_to_backfill(args.batch_size)

    while records:
        print(f"Processing batch of {len(records)} records...")

        for record in records:
            success = await update_record(record)
            if success:
                total_updated += 1
            else:
                total_failed += 1

            if total_updated % 50 == 0 and total_updated > 0:
                print(f"Progress: {total_updated} updated, {total_failed} failed")

        if args.max_records and total_updated >= args.max_records:
            print(f"Reached max records limit: {args.max_records}")
            break

        records = await get_records_to_backfill(args.batch_size)

    print("\nBackfill complete!")
    print(f"  Updated: {total_updated}")
    print(f"  Failed: {total_failed}")


if __name__ == "__main__":
    asyncio.run(main())
