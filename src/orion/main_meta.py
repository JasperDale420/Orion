import argparse
import asyncio
import contextlib
import sys
from datetime import datetime

from orion.agents.meta_search_agent import MetaSearchAgent
from orion.shared.logger import setup_struct_logger

# Setup Logger
logger = setup_struct_logger("orion.meta")

# Run daily 2 hours after market close (6 PM ET = 22:00 UTC on regular days)
SCHEDULED_HOUR_UTC = 22
SCHEDULED_MINUTE = 0


async def main() -> None:
    parser = argparse.ArgumentParser(description="Orion Meta-Search Agent CLI")
    parser.add_argument("--base-solver", type=str, required=True, help="Solver ID to evolve (e.g., 'v1_legacy')")
    parser.add_argument("--experiment-name", type=str, default="Manual Evolution", help="Experiment Name")
    parser.add_argument("--scheduled", action="store_true", help="Run in scheduled mode (daily after market close)")

    args = parser.parse_args()

    if args.scheduled:
        await run_scheduled(args.base_solver)
    else:
        logger.info(f"Starting Meta-Search Evolution for {args.base_solver}...")

        agent = MetaSearchAgent()
        try:
            await agent.run_evolution_cycle(base_solver_id=args.base_solver, experiment_name=args.experiment_name)
            logger.info("Evolution cycle completed.")
        except Exception as e:
            logger.error(f"Evolution cycle failed: {e}")
            sys.exit(1)


async def run_scheduled(base_solver: str) -> None:
    """Run in scheduled mode — execute daily at 6 PM ET (22:00 UTC) on weekdays."""
    from zoneinfo import ZoneInfo

    est = ZoneInfo("America/New_York")

    logger.info("Meta-search running in scheduled mode. Daily after market close on weekdays.")

    while True:
        now = datetime.now(est)

        is_weekday = now.weekday() < 5
        is_target_time = now.hour == 18 and now.minute == 0  # 6 PM ET

        if is_weekday and is_target_time:
            logger.info("Daily scheduled meta-search evolution starting", base_solver=base_solver)

            agent = MetaSearchAgent()
            try:
                await agent.run_evolution_cycle(
                    base_solver_id=base_solver,
                    experiment_name=f"Scheduled Daily {now.strftime('%Y-%m-%d')}",
                )
                logger.info("Daily scheduled meta-search evolution completed.")
            except Exception as e:
                logger.error(f"Daily meta-search evolution failed: {e}", exc_info=True)

            # Wait 1 hour to avoid re-triggering
            await asyncio.sleep(3600)
        else:
            await asyncio.sleep(60)


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
