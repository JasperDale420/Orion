import asyncio
import logging
from typing import Any, List

from dotenv import load_dotenv
from sqlalchemy import desc, select

# Load env before importing db
load_dotenv()

from orion.core.logging_config import setup_logging
from orion.shared.db_utils import db_query
from orion.storage.models import BronzeEvent

setup_logging()
logger = logging.getLogger("orion.query")


async def query_latest_events(limit: int = 10) -> None:
    async def fetch_events(session: Any) -> List[Any]:
        stmt = select(BronzeEvent).order_by(desc(BronzeEvent.event_ts_utc)).limit(limit)
        result = await session.execute(stmt)
        return result.scalars().all()

    events = await db_query(fetch_events)

    print(f"\n--- Latest {len(events)} Events ---")
    for e in events:
        print(f"[{e.event_ts_utc}] {e.source}::{e.event_type} | ID: {e.event_id}")
        # print(f"Payload: {e.payload}") # formatted or truncated
    print("----------------------------\n")


if __name__ == "__main__":
    asyncio.run(query_latest_events())
