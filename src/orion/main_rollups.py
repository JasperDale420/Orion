import asyncio
import logging
import signal
from types import FrameType
from typing import Optional

from orion.core.logging_config import setup_logging
from orion.jobs.rollup_job import RollupJob
from orion.storage.db import init_db

logger = logging.getLogger("orion.main_rollups")

SHUTDOWN = False


def handle_sigint(_signum: int, _frame: Optional[FrameType]) -> None:
    global SHUTDOWN
    SHUTDOWN = True


async def main() -> None:
    global SHUTDOWN
    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    setup_logging()
    logger.info("Starting Orion Rollup Service...")

    await init_db()

    job = RollupJob()
    while not SHUTDOWN:
        await job.run_once()
        await asyncio.sleep(job.loop_interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
