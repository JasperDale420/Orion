import asyncio
import json
import logging

from dotenv import load_dotenv
from sqlalchemy import select

load_dotenv()

from orion.storage.db import async_session_factory
from orion.storage.models_silver import SilverSignal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("orion.audit")


async def audit_silver():
    async with async_session_factory() as session:
        # Get latest 10 signals
        stmt = select(SilverSignal).order_by(SilverSignal.signal_ts_utc.desc()).limit(20)
        result = await session.execute(stmt)
        signals = result.scalars().all()

        print("\n--- Latest Silver Signals ---")
        for s in signals:
            # Format features for readability
            feats = json.dumps(s.features, indent=2)
            print(f"[{s.signal_ts_utc}] {s.ticker} | TYPE: {s.signal_type}")
            print(f"Features: {feats}")
            print("-" * 30)


if __name__ == "__main__":
    asyncio.run(audit_silver())
