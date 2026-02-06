import asyncio
import logging

from dotenv import load_dotenv

load_dotenv()

from orion.storage.db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("orion.runner")


async def run_strategist_cycle() -> None:
    # Ensure tables exist (esp new StrategyDecision)
    await init_db()

    # TODO: StrategistAgent is not defined. This script appears to be legacy/unused.
    # Commenting out the broken agent reference until this is properly implemented.
    logger.warning("This script (run_agent.py) is deprecated. Use main_execution.py instead.")
    return


if __name__ == "__main__":
    asyncio.run(run_strategist_cycle())
