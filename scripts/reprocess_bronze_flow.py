#!/usr/bin/env python3
"""
Re-process existing bronze UW_FLOW events through the fixed normalizer
to populate silver_uw_flow with correct aggressor/sweep values.
"""
import asyncio
import logging
import os

from dotenv import load_dotenv
from orion.core.logging_config import setup_logging

load_dotenv()

# Fix DB URL for local access
db_url = os.getenv("DB_URL", "")
if ":5432" in db_url:
    os.environ["DB_URL"] = db_url.replace(":5432", ":5440").replace("@timescaledb", "@localhost")

setup_logging()
logger = logging.getLogger("reprocess")


async def reprocess_bronze_to_silver():
    from orion.processing.normalizer import NormalizationEngine
    from orion.processing.persistence import persist_silver_from_bronze
    from orion.storage.db import async_session_factory, init_db
    from orion.storage.models import BronzeEvent
    from sqlalchemy import func, select

    await init_db()

    async with async_session_factory() as session:
        # Count UW_FLOW events in bronze
        count_result = await session.execute(select(func.count()).where(BronzeEvent.event_type == "UW_FLOW"))
        total = count_result.scalar()
        logger.info(f"Total UW_FLOW events in bronze: {total}")

        # Process in batches to avoid memory issues
        batch_size = 1000
        offset = 0
        total_processed = 0

        while True:
            result = await session.execute(
                select(BronzeEvent).where(BronzeEvent.event_type == "UW_FLOW").offset(offset).limit(batch_size)
            )
            events = list(result.scalars().all())

            if not events:
                break

            # Re-normalize each event's payload
            for e in events:
                if e.payload:
                    # Re-normalize with the fixed normalizer
                    normalized = NormalizationEngine.normalize_event("UW", "UW_FLOW", e.payload)
                    e.payload = normalized

            # Persist to silver
            await persist_silver_from_bronze(session, events)
            await session.commit()

            total_processed += len(events)
            logger.info(f"Processed batch: {len(events)} events (total: {total_processed}/{total})")

            offset += batch_size

        logger.info(f"Completed! Total processed: {total_processed}")


if __name__ == "__main__":
    asyncio.run(reprocess_bronze_to_silver())
