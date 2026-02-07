"""
Nightly Backfill Orchestrator.

Runs after market close (4:30pm ET) to ensure all price_target_labels
are fully populated with:
1. ML features (backfill_ml_features)
2. Price checkpoints (backfill_exit_columns)

Usage:
    python -m orion.jobs.nightly_backfill
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import NoReturn

from orion.core.market_schedule import MarketSchedule
from orion.jobs.backfill_exit_columns import run_backfill as run_exit_backfill
from orion.jobs.backfill_ml_features import run_backfill as run_ml_backfill
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db

logger = setup_struct_logger("orion.nightly_backfill")

BACKFILL_DELAY_MINUTES = 30
_MARKET_SCHEDULE = MarketSchedule()


def is_trading_day(dt: datetime) -> bool:
    """Check if date has an exchange session."""
    try:
        _open, close = _MARKET_SCHEDULE.get_open_close(dt)
    except RuntimeError:
        # Calendar unavailable fallback: weekday-only behavior.
        return dt.weekday() <= 4
    return close is not None


def _session_run_time_utc(dt: datetime) -> datetime | None:
    """Return session-aware run time for a day (close + delay), or None if no session."""
    try:
        _open, close = _MARKET_SCHEDULE.get_open_close(dt)
    except RuntimeError:
        if dt.weekday() <= 4:
            fallback_close_utc = dt.replace(hour=21, minute=0, second=0, microsecond=0)
            return fallback_close_utc + timedelta(minutes=BACKFILL_DELAY_MINUTES)
        return None

    if close is None:
        return None
    return close + timedelta(minutes=BACKFILL_DELAY_MINUTES)


def get_next_run_time() -> datetime:
    """Get the next scheduled run time (session close + delay on trading days)."""
    now_utc = datetime.now(timezone.utc)
    for day_offset in range(0, 14):
        candidate_day = now_utc + timedelta(days=day_offset)
        run_time = _session_run_time_utc(candidate_day)
        if run_time is not None and run_time > now_utc:
            return run_time

    # Defensive fallback if no session found in search window.
    fallback = now_utc + timedelta(days=1)
    while not is_trading_day(fallback):
        fallback += timedelta(days=1)
    fallback = fallback.replace(hour=21, minute=0, second=0, microsecond=0)
    return fallback + timedelta(minutes=BACKFILL_DELAY_MINUTES)


async def run_nightly_backfill() -> None:
    """Run all backfill jobs."""
    logger.info("Starting nightly backfill...")

    try:
        # Run ML features backfill (no limit - process all missing)
        logger.info("Running ML features backfill...")
        await run_ml_backfill(batch_size=100, limit=10000)
        logger.info("ML features backfill complete")

        # Run exit columns backfill
        logger.info("Running exit columns backfill...")
        await run_exit_backfill(batch_size=100, limit=10000)
        logger.info("Exit columns backfill complete")

        logger.info("Nightly backfill complete!")

    except Exception as e:
        logger.error(f"Nightly backfill failed: {e}", exc_info=True)


async def main() -> NoReturn:
    """Main loop - waits for market close and runs backfill daily."""
    await init_db()

    logger.info("Nightly backfill service started")

    while True:
        next_run = get_next_run_time()
        now = datetime.now(timezone.utc)
        wait_seconds = (next_run - now).total_seconds()

        logger.info(f"Next backfill scheduled for {next_run.isoformat()} (in {wait_seconds/3600:.1f} hours)")

        # Wait until scheduled time
        await asyncio.sleep(wait_seconds)

        # Run the backfill
        await run_nightly_backfill()

        # Sleep a bit before calculating next run to avoid race conditions
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
