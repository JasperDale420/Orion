import asyncio
import logging
import os

from orion.agents.meta_search_agent import MetaSearchAgent
from orion.storage.db import init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    """
    Entry point for the Meta-Search Loop.
    1. Ingests proposals from EOD agent (proposals/ -> DB).
    2. Processes pending edits (DB -> Backtest results).
    """

    # Initialize DB (if needed for standalone run)
    await init_db()

    agent = MetaSearchAgent()

    # Path to proposals
    # Assuming running from repo root or src
    # EOD agent writes to 'proposals' in CWD usually, need to coordinate path
    proposals_dir = os.getenv("ORION_PROPOSALS_DIR", "proposals")

    logger.info("Step 1: Ingesting Proposals...")
    await agent.ingest_proposals(proposals_dir)

    logger.info("Step 2: Processing Pending Edits...")
    await agent.process_pending_edits()

    logger.info("Meta-Search Loop Complete.")


if __name__ == "__main__":
    asyncio.run(main())
