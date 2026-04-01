import asyncio
import json
import logging
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import select

from orion.shared.db_utils import db_query

load_dotenv()

from orion.shared.logger import setup_logging
from orion.storage.models_gold import CandidateTrade

setup_logging()
logger = logging.getLogger("orion.audit_gold")


async def audit_gold() -> None:
    async def fetch_candidates(session: Any) -> list[Any]:
        stmt = select(CandidateTrade).order_by(CandidateTrade.timestamp_utc.desc()).limit(10)
        result = await session.execute(stmt)
        return result.scalars().all()

    candidates = await db_query(fetch_candidates)

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
