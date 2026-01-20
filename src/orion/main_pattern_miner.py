"""
Pattern Mining Service Entry Point.

Runs twice weekly (Monday and Friday) after market close to train ML models and extract trading patterns.
"""

import asyncio
import logging
import signal
from datetime import datetime, timedelta, timezone
from typing import Optional

from orion.core.market_schedule import MarketSchedule
from orion.ml.pattern_miner import run_all_pattern_mining
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db

logger = setup_struct_logger("orion.main_pattern_miner")

# Run 1 hour after market close on Monday and Friday
RUN_DAYS = [0, 4]  # Monday=0, Friday=4
POST_CLOSE_DELAY_MINUTES = 60


async def run_mining_job() -> None:
    """
    Run the pattern mining job once.
    """
    await init_db()

    logger.info(
        "Starting pattern mining job",
        extra={"event": "pattern_mining_start", "ts": datetime.now(timezone.utc).isoformat()},
    )

    try:
        summary = await run_all_pattern_mining()

        logger.info(
            "Pattern mining complete",
            extra={
                "event": "pattern_mining_complete",
                "insights_count": len(summary.insights),
                "alerts_count": len(summary.alerts),
            },
        )

        # Log the LLM-ready summary for debugging
        if summary.insights:
            logger.info(f"Generated ML insights:\n{summary.to_llm_prompt()}")

        if summary.alerts:
            for alert in summary.alerts:
                logger.warning(f"ML Alert: {alert}")

    except Exception as e:
        logger.error(f"Pattern mining failed: {e}", exc_info=True)
        raise


def get_next_run_close() -> Optional[datetime]:
    """
    Calculate the next Monday or Friday market close time.
    """
    schedule = MarketSchedule()
    now = datetime.now(timezone.utc)
    today = now.date()

    # Find next Monday or Friday
    for days_ahead in range(8):  # Look up to a week ahead
        check_date = today + timedelta(days=days_ahead)
        if check_date.weekday() in RUN_DAYS:
            # Check if it's today but already past close time
            if days_ahead == 0 and now.hour >= 21:  # Past 16:00 ET = 21:00 UTC
                continue

            try:
                _, close_time = schedule.get_open_close(
                    datetime.combine(check_date, datetime.min.time()).replace(tzinfo=timezone.utc)
                )
                if close_time:
                    return close_time
            except Exception:
                continue

    # Fallback: next Monday at 21:00 UTC
    days_until_monday = (0 - now.weekday()) % 7 or 7
    fallback_date = today + timedelta(days=days_until_monday)
    return datetime.combine(fallback_date, datetime.min.time()).replace(hour=21, minute=0, tzinfo=timezone.utc)


async def wait_for_next_run(shutdown_event: asyncio.Event) -> tuple[bool, bool]:
    """
    Wait until next Monday or Friday market close + delay, or check for drift more frequently.

    Returns:
        Tuple of (should_run_scheduled, check_drift):
        - (True, False) if scheduled Mon/Fri time reached
        - (False, True) if timeout reached for drift check
        - (False, False) if shutdown requested
    """
    close_time = get_next_run_close()
    if close_time is None:
        # Shouldn't happen, but fallback to 1 hour
        close_time = datetime.now(timezone.utc) + timedelta(hours=1)

    target_time = close_time + timedelta(minutes=POST_CLOSE_DELAY_MINUTES)
    now = datetime.now(timezone.utc)

    if now >= target_time:
        # Already past today's target, find next run day
        close_time = get_next_run_close()
        if close_time:
            target_time = close_time + timedelta(minutes=POST_CLOSE_DELAY_MINUTES)

    wait_seconds = max(0, (target_time - now).total_seconds())

    # Check for drift every hour (or until scheduled time)
    DRIFT_CHECK_INTERVAL = 3600  # 1 hour
    actual_wait = min(wait_seconds, DRIFT_CHECK_INTERVAL)

    day_name = target_time.strftime("%A")
    logger.info(
        f"Waiting until {day_name} {target_time.isoformat()} (checking drift every {DRIFT_CHECK_INTERVAL}s)",
        extra={
            "event": "schedule_wait",
            "target_time": target_time.isoformat(),
            "wait_seconds": wait_seconds,
            "actual_wait": actual_wait,
            "day": day_name,
        },
    )

    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=actual_wait)
        return (False, False)  # Shutdown requested
    except asyncio.TimeoutError:
        # Check if we hit the scheduled time
        if actual_wait == wait_seconds:
            return (True, False)  # Time to run scheduled
        else:
            return (False, True)  # Time to check drift


async def main() -> None:
    """
    Main entry point for pattern mining service.
    Runs continuously, executing mining job:
    - Monday and Friday after market close (scheduled)
    - Any day when drift flag is set by EOD agent (triggered)
    """
    await init_db()

    shutdown_event = asyncio.Event()

    def handle_signal(sig: int) -> None:
        logger.info(f"Received signal {sig}. Shutting down...")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: handle_signal(s))

    logger.info("Pattern mining service started. Running Monday/Friday + on drift trigger.")

    while not shutdown_event.is_set():
        run_scheduled, check_drift = await wait_for_next_run(shutdown_event)

        if run_scheduled:
            # Scheduled Mon/Fri run
            try:
                await run_mining_job()
            except Exception as e:
                logger.error(f"Mining job failed, will retry next run: {e}")
            await asyncio.sleep(60)

        elif check_drift:
            # Hourly drift check
            try:
                from orion.core.drift_trigger import check_and_clear_drift_flag

                flag_data = check_and_clear_drift_flag()
                if flag_data:
                    logger.info(
                        f"Drift trigger detected (max_psi={flag_data.get('max_psi', 'N/A')}), running mining",
                        extra={"event": "drift_triggered_mining", "flag_data": flag_data},
                    )
                    try:
                        await run_mining_job()
                    except Exception as e:
                        logger.error(f"Drift-triggered mining failed: {e}")
                    await asyncio.sleep(60)
            except Exception as e:
                logger.warning(f"Failed to check drift flag: {e}")
        # else: shutdown requested, loop will exit

    logger.info("Pattern mining service stopped.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
