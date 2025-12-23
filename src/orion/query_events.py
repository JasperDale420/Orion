import asyncio
import logging

from dotenv import load_dotenv
from sqlalchemy import desc, select

# Load env before importing db
load_dotenv()

from orion.storage.db import async_session_factory
from orion.storage.models import BronzeEvent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("orion.query")


async def query_latest_events(limit: int = 10):
    async with async_session_factory() as session:
        stmt = select(BronzeEvent).order_by(desc(BronzeEvent.event_ts_utc)).limit(limit)
        result = await session.execute(stmt)
        events = result.scalars().all()

        print(f"\n--- Latest {len(events)} Events ---")
        for e in events:
            print(f"[{e.event_ts_utc}] {e.source}::{e.event_type} | ID: {e.event_id}")
            # print(f"Payload: {e.payload}") # formatted or truncated
        print("----------------------------\n")


if __name__ == "__main__":
    asyncio.run(query_latest_events())
