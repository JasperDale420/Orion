import asyncio
import json
import logging
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import select

from orion.shared.db_utils import db_query

load_dotenv()

from orion.core.logging_config import setup_logging
from orion.storage.models_silver import SilverSignal

setup_logging()
logger = logging.getLogger("orion.audit")


async def audit_silver() -> None:
    async def fetch_signals(session: Any) -> list[Any]:
        stmt = select(SilverSignal).order_by(SilverSignal.signal_ts_utc.desc()).limit(20)
        result = await session.execute(stmt)
        return result.scalars().all()

    signals = await db_query(fetch_signals)

    print("\n--- Latest Silver Signals ---")
    for s in signals:
        # Format features for readability
        feats = json.dumps(s.features, indent=2)
        print(f"[{s.signal_ts_utc}] {s.ticker} | TYPE: {s.signal_type}")
        print(f"Features: {feats}")
        print("-" * 30)


if __name__ == "__main__":
    asyncio.run(audit_silver())
