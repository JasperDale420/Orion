"""
Data Quality Checker Service.

Runs hourly during market hours to detect data quality issues
(stale data, gaps, zero-valued bars, ML feature population).
"""

import argparse
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from orion.shared.async_main import run_service
from orion.shared.logger import setup_logging
from orion.jobs.data_quality_checker import run_quality_checks
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db

logger = setup_struct_logger("orion.data_quality")

CHECK_INTERVAL_SECONDS = 3600  # 1 hour
EST = ZoneInfo("America/New_York")

# Market hours (ET): pre-market 7 AM to post-market 8 PM
MARKET_START_HOUR = 7
MARKET_END_HOUR = 20


def configure_logging() -> None:
    setup_logging("orion-data-quality")


def _is_market_hours() -> bool:
    """Check if we're in extended market hours (7 AM - 8 PM ET, weekdays)."""
    now = datetime.now(EST)
    return now.weekday() < 5 and MARKET_START_HOUR <= now.hour < MARKET_END_HOUR


async def run_scheduled(shutdown_event: asyncio.Event) -> None:
    """Run data quality checks hourly during market hours."""
    await init_db()
    logger.info("Data quality checker started in scheduled mode (hourly during market hours).")

    while not shutdown_event.is_set():
        if _is_market_hours():
            try:
                summary = await run_quality_checks()
                anomaly_count = sum(len(v) for v in summary.values() if isinstance(v, list))
                logger.info(
                    "data_quality_check_complete",
                    anomalies=anomaly_count,
                    checks=len(summary),
                )
                if anomaly_count > 0:
                    logger.warning(
                        "data_quality_anomalies_detected",
                        anomaly_count=anomaly_count,
                        summary_keys=list(summary.keys()),
                    )
            except Exception as e:
                logger.error(f"Data quality check failed: {e}", exc_info=True)
        else:
            logger.debug("Outside market hours, skipping data quality check.")

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=CHECK_INTERVAL_SECONDS)
            break
        except TimeoutError:
            pass

    logger.info("Data quality checker stopped.")


async def run_once() -> None:
    await init_db()
    summary = await run_quality_checks()
    anomaly_count = sum(len(v) for v in summary.values() if isinstance(v, list))
    logger.info(f"Data quality check complete. {anomaly_count} anomalies found.")


if __name__ == "__main__":
    configure_logging()

    parser = argparse.ArgumentParser(description="Orion Data Quality Checker")
    parser.add_argument("--scheduled", action="store_true", help="Run in scheduled loop mode")
    args = parser.parse_args()

    if args.scheduled:
        # run_scheduled() calls init_db() itself, so skip the helper's init.
        run_service("orion.data_quality", run_scheduled, init_database=False)
    else:
        asyncio.run(run_once())
