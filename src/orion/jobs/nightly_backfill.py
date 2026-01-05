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
from datetime import datetime, time, timezone, timedelta
from typing import NoReturn

from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db
from orion.jobs.backfill_ml_features import run_backfill as run_ml_backfill
from orion.jobs.backfill_exit_columns import run_backfill as run_exit_backfill

logger = setup_struct_logger("orion.nightly_backfill")

# Run at 4pm ET daily (1 hour before ML training on Mon/Fri)
BACKFILL_HOUR_ET = 16
BACKFILL_MINUTE_ET = 0


def is_trading_day(dt: datetime) -> bool:
    """Check if date is a trading day (weekday)."""
    return dt.weekday() <= 4


def get_next_run_time() -> datetime:
    """Get the next scheduled run time (4pm ET daily on trading days)."""
    now_utc = datetime.now(timezone.utc)
    
    # Convert to ET (UTC-5)
    et_offset = timedelta(hours=-5)
    now_et = now_utc + et_offset
    
    # Target time today at 4pm ET
    target_et = now_et.replace(
        hour=BACKFILL_HOUR_ET,
        minute=BACKFILL_MINUTE_ET,
        second=0,
        microsecond=0
    )
    
    # If we've already passed today's target, move to tomorrow
    if now_et >= target_et:
        target_et += timedelta(days=1)
    
    # Skip weekends
    while not is_trading_day(target_et):
        target_et += timedelta(days=1)
    
    # Convert back to UTC
    return target_et - et_offset


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
