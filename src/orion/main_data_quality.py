"""
Data Quality Checker Service.

Runs hourly during market hours to detect data quality issues
(stale data, gaps, zero-valued bars, ML feature population).
"""

import argparse
import asyncio
import contextlib
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.exc import SQLAlchemyError

from orion.core.service_lease import acquire_service_lease, renew_service_lease
from orion.shared.async_main import run_service
from orion.shared.logger import setup_logging
from orion.jobs.data_quality_checker import run_quality_checks
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db, wait_for_db

logger = setup_struct_logger("orion.data_quality")

CHECK_INTERVAL_SECONDS = 3600  # 1 hour
EST = ZoneInfo("America/New_York")

# Market hours (ET): pre-market 7 AM to post-market 8 PM
MARKET_START_HOUR = 7
MARKET_END_HOUR = 20

# Single-instance lease (SystemStatus row `service_lease_data_quality`).
# The scheduled loop sleeps an hour between checks — far longer than the
# 120s stale window — so a background heartbeat renews at half the window.
_LEASE_SERVICE_ID = "data_quality"
_LEASE_RENEW_INTERVAL_SECONDS = 60


class LeaseLostError(RuntimeError):
    """Raised by the renewal task when the data_quality lease is stolen.

    data_quality executes no trades, but a silent dual data-quality writer
    is still wrong: it doubles checks and can race on shared state. So a
    stolen lease is fatal — it sets the shutdown event to stop the loop and
    propagates so ``run_service`` exits non-zero.
    """


def configure_logging() -> None:
    setup_logging("orion-data-quality")


def _is_market_hours() -> bool:
    """Check if we're in extended market hours (7 AM - 8 PM ET, weekdays)."""
    now = datetime.now(EST)
    return now.weekday() < 5 and MARKET_START_HOUR <= now.hour < MARKET_END_HOUR


async def _lease_is_ours(run_id: str) -> bool:
    """Return True iff the lease row still records ``run_id`` as owner.

    ``renew_service_lease`` swallows a stolen lease (logs and returns); we
    re-read the row ourselves to make loss fatal here without changing the
    shared renewal helper's contract.
    """
    from sqlalchemy import select

    from orion.core.service_lease import _service_lease_key
    from orion.storage.db import async_session_factory
    from orion.storage.models import SystemStatus

    key = _service_lease_key(_LEASE_SERVICE_ID)
    async with async_session_factory() as session:
        existing = (await session.execute(select(SystemStatus).where(SystemStatus.key == key))).scalars().first()
        if existing is None:
            return True
        return f"run_id={run_id}" in (existing.details or "")


async def _renew_lease_forever(run_id: str, shutdown_event: asyncio.Event) -> None:
    """Heartbeat the single-instance lease until cancelled.

    On a stolen lease, set ``shutdown_event`` so the daemon loop stops
    promptly, then raise ``LeaseLostError`` so ``run_scheduled`` surfaces a
    non-zero exit.
    """
    while True:
        await asyncio.sleep(_LEASE_RENEW_INTERVAL_SECONDS)
        await renew_service_lease(_LEASE_SERVICE_ID, run_id)
        try:
            lease_is_ours = await _lease_is_ours(run_id)
        except (OSError, SQLAlchemyError) as exc:
            # A transient DB blip (e.g. a Docker VM restart) here is "unknown,
            # retry" — not confirmed loss (which is fatal below) and not
            # confirmed ownership. Skip this tick and let the next one check
            # again rather than crashing the heartbeat for the rest of the
            # process's life.
            logger.warning(
                "service_lease_check_failed",
                extra={
                    "event_type": "SERVICE_LEASE_CHECK_FAILED",
                    "service_id": _LEASE_SERVICE_ID,
                    "run_id": run_id,
                    "error": str(exc),
                },
            )
            continue
        if not lease_is_ours:
            shutdown_event.set()
            logger.critical(
                "data_quality_lease_lost",
                extra={"event_type": "DATA_QUALITY_LEASE_LOST", "run_id": run_id},
            )
            raise LeaseLostError(f"data_quality lease (run_id={run_id}) was taken by another owner; stopping.")


async def run_scheduled(shutdown_event: asyncio.Event) -> None:
    """Run data quality checks hourly during market hours."""
    # Wait for the DB before init_db so a transient outage doesn't crash the
    # service into a launchd restart loop (see main_execution.py). cancel_event
    # lets a SIGTERM during the wait abort startup promptly.
    await wait_for_db(cancel_event=shutdown_event)
    await init_db()

    # Single-instance lease for the daemon only (Wave C RB.4). data_quality
    # has no execution risk, so the lease is purely a deploy-safety guard
    # against a stray docker copy and the native daemon co-running. A plain
    # one-shot `run_once()` does NOT acquire it. Raises RuntimeError on a
    # competing fresh lease; that propagates so run_service exits non-zero.
    lease_run_id = await acquire_service_lease(_LEASE_SERVICE_ID)
    # Start the heartbeat IMMEDIATELY after acquisition, before the loop, so
    # the lease never ages out and a stolen lease is detected promptly. The
    # task also sets shutdown_event on loss so the loop wakes and exits.
    renew_task = asyncio.create_task(_renew_lease_forever(lease_run_id, shutdown_event))

    logger.info("Data quality checker started in scheduled mode (hourly during market hours).")

    try:
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
    finally:
        renew_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await renew_task

    # If the heartbeat stopped because the lease was stolen, surface it as a
    # fatal non-zero exit (the loop above already drained on the event).
    if renew_task.cancelled() is False:
        exc = renew_task.exception()
        if exc is not None:
            raise exc

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
