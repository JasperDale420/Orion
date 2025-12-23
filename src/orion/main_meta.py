import argparse
import asyncio
import sys

from orion.agents.meta_search_agent import MetaSearchAgent
from orion.shared.logger import setup_struct_logger

# Setup Logger
logger = setup_struct_logger("orion.meta")


async def main():
    parser = argparse.ArgumentParser(description="Orion Meta-Search Agent CLI")
    parser.add_argument("--base-solver", type=str, required=True, help="Solver ID to evolve (e.g., 'v1_legacy')")
    parser.add_argument("--experiment-name", type=str, default="Manual Evolution", help="Experiment Name")

    args = parser.parse_args()

    logger.info(f"Starting Meta-Search Evolution for {args.base_solver}...")

    agent = MetaSearchAgent()
    try:
        await agent.run_evolution_cycle(base_solver_id=args.base_solver, experiment_name=args.experiment_name)
        logger.info("Evolution cycle completed.")
    except Exception as e:
        logger.error(f"Evolution cycle failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
