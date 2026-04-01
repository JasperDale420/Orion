"""
EOD Agent Service - Daily End-of-Day Review Agent.

Convenience entry point for manual/standalone EOD runs. The primary automated
trigger lives in ``ingestion/service.py`` (``_check_eod_trigger``, ~line 426),
which fires EODReviewAgent from within the ingestion loop at 01:05 UTC daily.
This standalone service provides an independent scheduling mechanism using
MarketSchedule (30 min after market close) and can be run as a separate Docker
service (``eod-agent``) or invoked manually.
"""

import asyncio
import contextlib
import signal
from datetime import UTC, datetime, timedelta

from orion.agents.eod_review_agent import EODReviewAgent
from orion.shared.logger import setup_logging
from orion.core.market_schedule import MarketSchedule
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.main_eod")

# Time after market close to run EOD review (in minutes)
EOD_DELAY_MINUTES = 30


class EODService:
    """
    Service that runs EOD Review Agent after market close on trading days.
    """

    def __init__(self) -> None:
        self.schedule = MarketSchedule()
        self.agent = EODReviewAgent()
        self._shutdown_event = asyncio.Event()

    async def run(self) -> None:
        """
        Main service loop. Waits for market close + delay, runs EOD review,
        then waits for next trading day.
        """
        logger.info("EOD Service starting...")

        while not self._shutdown_event.is_set():
            try:
                await self._wait_for_eod_window()

                if self._shutdown_event.is_set():
                    break

                await self._run_eod_review()

            except Exception as e:
                logger.error(
                    f"EOD Service error: {e}",
                    extra={"event": "eod_service_error"},
                    exc_info=True,
                )
                # Wait before retry
                await asyncio.sleep(60)

        logger.info("EOD Service shutting down...")

    async def _wait_for_eod_window(self) -> None:
        """
        Wait until the EOD window (30 min after market close).
        On non-trading days, waits until next market open then close.
        """
        now = datetime.now(UTC)

        try:
            _, close_time = self.schedule.get_open_close(now)

            if close_time is None:
                # Not a trading day, wait for next market open
                next_open = self.schedule.get_next_market_open(now)
                wait_seconds = (next_open - now).total_seconds()
                logger.info(
                    f"No trading session today. Waiting for next market open at {next_open}",
                    extra={
                        "event": "eod_wait_next_open",
                        "next_open": next_open.isoformat(),
                        "wait_seconds": wait_seconds,
                    },
                )
                await self._interruptible_sleep(wait_seconds)
                return

            # Calculate EOD window (close + delay)
            eod_time = close_time + timedelta(minutes=EOD_DELAY_MINUTES)

            if now < eod_time:
                # Wait until EOD window
                wait_seconds = (eod_time - now).total_seconds()
                logger.info(
                    f"Waiting for EOD window at {eod_time}",
                    extra={
                        "event": "eod_wait_window",
                        "market_close": close_time.isoformat(),
                        "eod_time": eod_time.isoformat(),
                        "wait_seconds": wait_seconds,
                    },
                )
                await self._interruptible_sleep(wait_seconds)
            else:
                # Already past EOD window for today
                logger.info(
                    "Already past today's EOD window. Waiting for next trading day.",
                    extra={"event": "eod_past_window", "current_time": now.isoformat()},
                )
                next_open = self.schedule.get_next_market_open(now)
                wait_seconds = (next_open - now).total_seconds()
                await self._interruptible_sleep(wait_seconds)

        except RuntimeError as e:
            # Calendar not available - wait and retry
            logger.warning(f"Market calendar unavailable: {e}. Retrying in 5 minutes.")
            await self._interruptible_sleep(300)

    async def _run_eod_review(self) -> None:
        """
        Execute the EOD review for today.
        """
        today = datetime.now(UTC).date()

        logger.info(
            f"Starting EOD review for {today}",
            extra={"event": "eod_review_start", "date": str(today)},
        )

        try:
            result = await self.agent.run_review(today)

            logger.info(
                "EOD review completed",
                extra={
                    "event": "eod_review_complete",
                    "date": str(today),
                    "run_id": result.get("run_id"),
                    "proposals_count": result.get("proposals_count", 0),
                    "report_path": result.get("report_path"),
                },
            )

            # Process solver mutations if any (pre-filtered by EODReviewAgent)
            mutation_proposals = result.get("solver_edit_proposals", [])

            if mutation_proposals:
                await self._process_solver_mutations(mutation_proposals)

        except Exception as e:
            logger.error(
                f"EOD review failed for {today}: {e}",
                extra={"event": "eod_review_failed", "date": str(today)},
                exc_info=True,
            )
            raise

    async def _process_solver_mutations(self, proposals: list) -> None:
        """Delegate solver mutation processing to dedicated module."""
        from orion.agents.solver_mutation_processor import process_solver_mutations

        await process_solver_mutations(proposals)

    async def _interruptible_sleep(self, seconds: float) -> None:
        """
        Sleep that can be interrupted by shutdown signal.
        """
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                self._shutdown_event.wait(),
                timeout=max(0, seconds),
            )

    def shutdown(self) -> None:
        """
        Signal the service to shut down gracefully.
        """
        logger.info("Shutdown signal received")
        self._shutdown_event.set()


async def main() -> None:
    """
    Entry point for EOD service.
    """
    service = EODService()

    # Setup signal handlers
    loop = asyncio.get_running_loop()

    def handle_signal() -> None:
        service.shutdown()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_signal)

    await service.run()


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())
