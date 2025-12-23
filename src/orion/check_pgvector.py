import asyncio
import logging

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()
from orion.storage.db import async_session_factory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("check_pgvector")


async def check_vector():
    async with async_session_factory() as session:
        try:
            logger.info("Attempting to create vector extension...")
            await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector CASCADE;"))
            await session.commit()
            logger.info("SUCCESS: pgvector extension enabled!")
        except Exception as e:
            logger.error(f"FAILURE: Could not enable pgvector. Error: {e}")


if __name__ == "__main__":
    asyncio.run(check_vector())
