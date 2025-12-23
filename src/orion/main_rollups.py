import asyncio
import logging
import signal

from orion.jobs.rollup_job import RollupJob
from orion.storage.db import init_db

logger = logging.getLogger("orion.main_rollups")

SHUTDOWN = False


def handle_sigint(_signum, _frame):
    global SHUTDOWN
    SHUTDOWN = True


async def main() -> None:
    global SHUTDOWN
    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    logging.basicConfig(level=logging.INFO)
    logger.info("Starting Orion Rollup Service...")

    await init_db()

    job = RollupJob()
    while not SHUTDOWN:
        await job.run_once()
        await asyncio.sleep(job.loop_interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
