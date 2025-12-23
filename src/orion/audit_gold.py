import asyncio
import json
import logging

from dotenv import load_dotenv
from sqlalchemy import select

load_dotenv()

from orion.storage.db import async_session_factory
from orion.storage.models_gold import CandidateTrade

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("orion.audit_gold")


async def audit_gold():
    async with async_session_factory() as session:
        # Get latest 10 candidates
        stmt = select(CandidateTrade).order_by(CandidateTrade.timestamp_utc.desc()).limit(10)
        result = await session.execute(stmt)
        candidates = result.scalars().all()

        print("\n--- Latest Candidate Trades (GOLD) ---")
        if not candidates:
            print("No candidates found yet.")

        for c in candidates:
            # Format evidence for readability
            evidence = json.dumps(c.evidence, indent=2)
            print(f"[{c.timestamp_utc}] {c.ticker} | {c.direction} | Rule: {c.rule_id}")
            print(f"Confidence: {c.confidence}")
            print(f"Evidence: {evidence}")
            print("-" * 30)


if __name__ == "__main__":
    asyncio.run(audit_gold())
