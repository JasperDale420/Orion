"""
EOD Agent Service - Daily End-of-Day Review Agent.

Runs automatically after market close on trading days.
Uses MarketSchedule to detect trading sessions and runs EODReviewAgent
30 minutes after market close.
"""

import asyncio
import contextlib
import signal
from datetime import UTC, datetime, timedelta

from orion.agents.eod_review_agent import EODReviewAgent
from orion.core.logging_config import setup_logging
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

            # Process solver mutations if any
            proposals = result.get("proposals", [])
            mutation_proposals = [p for p in proposals if p.get("type") == "solver_mutation"]

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
        """
        Process solver_mutation proposals using iterative refinement loop.

        Flow:
        1. Build initial SolverConfig from proposal
        2. Call MetaSearchAgent.refine_and_promote() which:
           - Backtests the solver
           - If score < threshold, asks MetaAgent for refinement
           - Repeats until threshold met or max iterations reached
           - Auto-promotes to paper stage if threshold met
        """
        from sqlalchemy import select

        from orion.agents.meta_search_agent import MetaSearchAgent
        from orion.core.id_utils import deterministic_solver_id
        from orion.core.solver_schema import EditOp, EditOpType, SolverConfig, SolverEdit
        from orion.shared.db_utils import db_query
        from orion.storage.models_solvers import Solver

        logger.info(
            f"Processing {len(proposals)} solver mutation proposals with refinement loop",
            extra={"event": "solver_mutations_start", "count": len(proposals)},
        )

        meta_search = MetaSearchAgent()
        promoted_count = 0
        failed_count = 0

        for proposal in proposals:
            try:
                mutation = proposal.get("mutation", {})
                base_solver_id = mutation.get("base_solver_id")
                ops_data = mutation.get("ops", [])

                if not ops_data:
                    logger.warning("Skipping mutation with no ops")
                    continue

                # Load base solver config
                base_config = None
                if base_solver_id:

                    async def fetch_base(session, sid=base_solver_id):
                        stmt = select(Solver).where(Solver.solver_id == sid)
                        result = await session.execute(stmt)
                        return result.scalars().first()

                    base_solver = await db_query(fetch_base)
                    if base_solver:
                        base_config = SolverConfig(**base_solver.config)

                if not base_config:
                    # Use default baseline config
                    from orion.core.solver_schema import ExitLogic, SolverFeatures, SolverRiskConfig

                    base_config = SolverConfig(
                        version_id="baseline",
                        rules=[],
                        features=SolverFeatures(),
                        risk=SolverRiskConfig(),
                        exit_logic=ExitLogic(
                            take_profit_atr_multiple=2.0,
                            stop_loss_atr_multiple=1.0,
                        ),
                    )

                # Convert ops to EditOp objects
                ops = []
                for op_data in ops_data:
                    try:
                        op_type = EditOpType(op_data.get("op", "modify_param"))
                        ops.append(
                            EditOp(
                                op=op_type,
                                param_name=op_data.get("param_name"),
                                feature_name=op_data.get("feature_name"),
                                new_value=op_data.get("new_value"),
                                reasoning=op_data.get("reasoning", "EOD agent generated"),
                            )
                        )
                    except Exception as e:
                        logger.warning(f"Skipping invalid op: {e}")
                        continue

                if not ops:
                    continue

                # Generate new solver ID
                new_solver_id = deterministic_solver_id(
                    base_solver_id=base_solver_id or "baseline",
                    edit_ops={"ops": [o.model_dump(mode="json") for o in ops]},
                    prefix="eod",
                )

                # Apply edit to create initial config
                solver_edit = SolverEdit(
                    base_solver_id=base_solver_id or "baseline",
                    new_solver_id=new_solver_id,
                    generated_by="eod_agent",
                    ops=ops,
                )

                new_config = meta_search.apply_edit(base_config, solver_edit)

                logger.info(
                    f"Starting refinement loop for solver {new_solver_id}",
                    extra={
                        "event": "refinement_start",
                        "solver_id": new_solver_id,
                        "base_solver_id": base_solver_id,
                        "ops_count": len(ops),
                    },
                )

                # Run refinement loop - backtests and refines until threshold met
                promoted_id = await meta_search.refine_and_promote(
                    solver_id=new_solver_id,
                    config=new_config,
                    base_solver_id=base_solver_id or "baseline",
                )

                if promoted_id:
                    promoted_count += 1
                    logger.info(
                        f"Solver {promoted_id} promoted to paper trading",
                        extra={"event": "solver_promoted_to_paper", "solver_id": promoted_id},
                    )
                else:
                    failed_count += 1

            except Exception as e:
                logger.error(f"Failed to process mutation: {e}", exc_info=True)
                failed_count += 1

        logger.info(
            f"Solver mutation processing complete: {promoted_count} promoted, {failed_count} failed/gave up",
            extra={
                "event": "solver_mutations_complete",
                "promoted": promoted_count,
                "failed": failed_count,
            },
        )

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
