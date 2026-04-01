import asyncio
import logging
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()
from orion.shared.logger import setup_logging
from orion.shared.db_utils import db_write

setup_logging()
logger = logging.getLogger("check_pgvector")


async def check_vector() -> None:
    async def enable_extension(session: Any) -> None:
        try:
            logger.info("Attempting to create vector extension...")
            await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector CASCADE;"))
            logger.info("SUCCESS: pgvector extension enabled!")
        except Exception as e:
            logger.error(f"FAILURE: Could not enable pgvector. Error: {e}")
            raise

    await db_write(enable_extension)


if __name__ == "__main__":
    asyncio.run(check_vector())
