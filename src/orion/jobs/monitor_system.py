import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from orion.config import system_settings
from orion.shared.db_utils import db_write
from orion.shared.logger import setup_struct_logger
from orion.storage.models import SystemStatus
from orion.storage.models_dlq import DeadLetterQueue

logger = setup_struct_logger("orion.monitor")

# Configurable Thresholds
HEARTBEAT_LAG_THRESHOLD_SECONDS = system_settings.monitor_lag_threshold
DLQ_LOOKBACK_MINUTES = system_settings.monitor_dlq_lookback


async def check_heartbeats(session: Any) -> None:
    """
    Checks SystemStatus table for stale heartbeats.
    """
    stmt = select(SystemStatus)
    result = await session.execute(stmt)
    statuses = result.scalars().all()

    now = datetime.now(UTC)

    for s in statuses:
        if not s.last_updated_utc:
            continue

        # Ensure timezone awareness
        last_update = s.last_updated_utc
        if last_update.tzinfo is None:
            logger.warning(
                f"Naive heartbeat timestamp detected for {s.key}; assuming UTC",
                extra={"event_type": "HEARTBEAT_NAIVE_TIMESTAMP", "key": s.key},
            )
            last_update = last_update.replace(tzinfo=UTC)

        lag_seconds = (now - last_update).total_seconds()

        if lag_seconds > HEARTBEAT_LAG_THRESHOLD_SECONDS:
            logger.error(
                f"ALERT: Stale Heartbeat for {s.key}",
                extra={
                    "event_type": "HEARTBEAT_STALE",
                    "key": s.key,
                    "lag_seconds": lag_seconds,
                    "threshold_seconds": HEARTBEAT_LAG_THRESHOLD_SECONDS,
                    "status": s.status,
                },
            )
        else:
            logger.info(
                f"Heartbeat OK: {s.key}",
                extra={"event_type": "HEARTBEAT_OK", "key": s.key, "lag_seconds": lag_seconds},
            )


async def check_dlq(session: Any) -> None:
    """
    Checks DeadLetterQueue for recent failures.
    """
    now = datetime.now(UTC)
    lookback_time = now - timedelta(minutes=DLQ_LOOKBACK_MINUTES)

    # Count failed items since lookback
    stmt = (
        select(func.count(DeadLetterQueue.id))
        .where(DeadLetterQueue.timestamp_utc >= lookback_time)
        .where(DeadLetterQueue.status == "FAILED")
    )

    result = await session.execute(stmt)
    count = result.scalar() or 0

    if count > 0:
        logger.error(
            f"ALERT: {count} new Failures in DLQ",
            extra={"event_type": "DLQ_SPIKE", "count": count, "lookback_minutes": DLQ_LOOKBACK_MINUTES},
        )

        # Optional: Print details of latest failure
        stmt_latest = (
            select(DeadLetterQueue)
            .where(DeadLetterQueue.timestamp_utc >= lookback_time)
            .where(DeadLetterQueue.status == "FAILED")
            .order_by(DeadLetterQueue.timestamp_utc.desc())
            .limit(1)
        )
        res_latest = await session.execute(stmt_latest)
        latest = res_latest.scalars().first()
        if latest:
            logger.error(
                "Latest DLQ error",
                extra={
                    "event_type": "DLQ_LATEST",
                    "error_message": latest.error_message,
                    "source": latest.source,
                    "event_type_dlq": latest.event_type,
                },
            )

    else:
        logger.info("DLQ OK", extra={"event_type": "DLQ_OK", "lookback_minutes": DLQ_LOOKBACK_MINUTES})


async def run_monitor_loop() -> None:
    """
    Main monitoring loop.
    """
    logger.info("Starting System Monitor...")

    while True:
        try:

            async def monitor_tasks(session: Any) -> None:
                await check_heartbeats(session)
                await check_dlq(session)

            await db_write(monitor_tasks)

        except Exception as e:
            logger.error(f"Monitor Loop Failed: {e}", exc_info=True)

        await asyncio.sleep(60)  # Run every minute


if __name__ == "__main__":
    try:
        asyncio.run(run_monitor_loop())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Monitor Stopped.")
