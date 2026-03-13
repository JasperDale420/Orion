"""
Nightly Backfill Orchestrator.

Runs after market close (4:30pm ET) to refresh Heber-backed
feature enrichment for historical label training records.
1. ML features (backfill_ml_features)

Usage:
    python -m orion.jobs.nightly_backfill
"""

import asyncio
from datetime import UTC, datetime, timedelta

from orion.config import SystemSettings
from orion.core.market_schedule import MarketSchedule
from orion.jobs.backfill_ml_features import run_backfill as run_ml_backfill
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db

logger = setup_struct_logger("orion.nightly_backfill")

BACKFILL_DELAY_MINUTES = 30
_MARKET_SCHEDULE = MarketSchedule()


def _legacy_label_pipeline_control() -> tuple[bool, str, str]:
    settings = SystemSettings()

    specific_key = "ORION_ENABLE_LEGACY_NIGHTLY_BACKFILL"
    if settings.legacy_nightly_backfill_enabled is not None:
        enabled = settings.legacy_nightly_backfill_enabled
        raw = "true" if enabled else "false"
        return enabled, specific_key, raw

    global_key = "ORION_ENABLE_LEGACY_LABEL_PIPELINES"
    enabled = settings.legacy_label_pipelines_enabled
    raw = "true" if enabled else "false"
    return enabled, global_key, raw


def _legacy_label_pipelines_enabled() -> bool:
    enabled, _key, _raw = _legacy_label_pipeline_control()
    return enabled


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
    now_utc = datetime.now(UTC)
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
    """Run nightly Heber-first backfill jobs."""
    logger.info("Starting nightly backfill...")

    try:
        # Run ML features backfill (no limit - process all missing)
        logger.info("Running ML features backfill...")
        await run_ml_backfill(batch_size=100, limit=10000)
        logger.info("ML features backfill complete")

        logger.info("Nightly backfill complete!")

    except Exception as e:
        logger.error(f"Nightly backfill failed: {e}", exc_info=True)


async def main() -> None:
    """Main loop - waits for market close and runs backfill daily."""
    enabled, control_key, control_raw = _legacy_label_pipeline_control()
    if not enabled:
        logger.warning(
            "Legacy nightly backfill disabled by config",
            extra={
                "event": "legacy_label_pipeline_disabled",
                "pipeline": "orion.jobs.nightly_backfill",
                "control_key": control_key,
                "control_raw": control_raw,
            },
        )
        return

    await init_db()

    logger.info("Nightly backfill service started")

    while True:
        next_run = get_next_run_time()
        now = datetime.now(UTC)
        wait_seconds = (next_run - now).total_seconds()

        logger.info(f"Next backfill scheduled for {next_run.isoformat()} (in {wait_seconds / 3600:.1f} hours)")

        # Wait until scheduled time
        await asyncio.sleep(wait_seconds)

        # Run the backfill
        await run_nightly_backfill()

        # Sleep a bit before calculating next run to avoid race conditions
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
