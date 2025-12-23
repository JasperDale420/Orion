import asyncio
import logging
from datetime import datetime, timedelta, timezone

from orion.storage.db import async_session_factory
from orion.storage.models import BronzeEvent
from orion.storage.models_silver import SilverAlpacaBar
from sqlalchemy import func, select

logger = logging.getLogger(__name__)


async def run_reconciliation(lookback_days: int = 7):
    """
    PRD 17.3: Reconciliation Job.
    Checks for missing bars between Bronze (Raw) and Silver (Normalized) layers.
    Triggers alerts (and eventually backfills) for discrepancies.
    """
    logger.info(f"Starting Data Reconciliation (Lookback: {lookback_days} days)...")

    start_date = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    async with async_session_factory() as session:
        # 1. Get Bronze Counts (Grouped by Ticker, Date)
        # Using date_trunc('day', ...) for Postgres or substr for SQLite compatibility if possible
        # Since we use Postgres in prod, we assume standard SQL.
        # For cross-compatibility with SQLite tests, we might need a simpler grouping or use Python processing if volume is low.
        # Given "big data" isn't here yet, we can fetch aggregates.

        # NOTE: func.date() works in many dialects.

        # Bronze: ALPACA_BAR_1M
        stmt_bronze = (
            select(
                BronzeEvent.ticker,
                func.date(BronzeEvent.event_ts_utc).label("event_date"),
                func.count(BronzeEvent.event_id).label("count"),
            )
            .where(BronzeEvent.event_type == "ALPACA_BAR_1M")
            .where(BronzeEvent.event_ts_utc >= start_date)
            .group_by(BronzeEvent.ticker, func.date(BronzeEvent.event_ts_utc))
        )

        # Silver: ALPACA_BARS
        stmt_silver = (
            select(
                SilverAlpacaBar.ticker,
                func.date(SilverAlpacaBar.bar_start_ts_utc).label("bar_date"),
                func.count().label("count"),
            )
            .where(SilverAlpacaBar.bar_start_ts_utc >= start_date)
            .group_by(SilverAlpacaBar.ticker, func.date(SilverAlpacaBar.bar_start_ts_utc))
        )

        try:
            res_bronze = await session.execute(stmt_bronze)
            bronze_counts = {}  # (ticker, date_str) -> count
            for row in res_bronze.all():
                key = (row.ticker, str(row.event_date))
                bronze_counts[key] = row.count

            res_silver = await session.execute(stmt_silver)
            silver_counts = {}
            for row in res_silver.all():
                key = (row.ticker, str(row.bar_date))
                silver_counts[key] = row.count

            # Compare
            discrepancies = 0

            # Check Bronze -> Silver (Missing in Silver?)
            for key, b_count in bronze_counts.items():
                s_count = silver_counts.get(key, 0)
                if s_count != b_count:
                    ticker, date_str = key
                    logger.error(
                        f"DATA GAP: {ticker} on {date_str}. Bronze={b_count}, Silver={s_count}. "
                        f"Missing {b_count - s_count} bars."
                    )
                    discrepancies += 1
                    # TODO: Trigger Backfill Service here
                    # await backfill_service.trigger(ticker, date_str)

            # Check Silver without Bronze? (Phantom data - unlikely but possible if manual insert)
            for key, s_count in silver_counts.items():
                if key not in bronze_counts:
                    ticker, date_str = key
                    logger.warning(f"DATA ORPHAN: {ticker} on {date_str} exists in Silver ({s_count}) but not Bronze.")

            if discrepancies == 0:
                logger.info("Reconciliation Complete: No gaps found.")
            else:
                logger.warning(f"Reconciliation Complete: Found {discrepancies} gaps.")

        except Exception as e:
            logger.error(f"Reconciliation Failed: {e}")


if __name__ == "__main__":
    # Setup simple logging for standalone run
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_reconciliation(lookback_days=30))
    except (KeyboardInterrupt, SystemExit):
        pass
