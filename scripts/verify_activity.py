import asyncio
import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import text

# Load env for host access to DB
load_dotenv()
os.environ["DB_URL"] = os.getenv("DB_URL").replace(":5432", ":5440").replace("@timescaledb", "@localhost")

from orion.storage.db import async_session_factory, init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify")


async def verify():
    await init_db()

    async with async_session_factory() as session:
        # 1. Check Bronze Events (Backfilled?)
        q_bronze = text("SELECT source, count(*) FROM bronze_events WHERE event_ts_utc > :ts GROUP BY source")
        res_bronze = await session.execute(q_bronze, {"ts": datetime(2025, 12, 23, tzinfo=timezone.utc)})
        logger.info("Bronze Events Today by Source:")
        total = 0
        for row in res_bronze:
            logger.info(f" - {row[0]}: {row[1]}")
            total += row[1]
        logger.info(f"Total Bronze Events: {total}")

        # 2. Check Candidate Trades (Gold)
        q_candidates = text("SELECT count(*) FROM candidate_trades WHERE created_at_utc > :ts")
        res_cand = await session.execute(q_candidates, {"ts": datetime(2025, 12, 23, tzinfo=timezone.utc)})
        logger.info(f"Candidate Trades Today: {res_cand.scalar()}")

        # 3. Check Strategy Decisions
        q_decisions = text(
            "SELECT count(*), decision FROM strategy_decisions WHERE timestamp_utc > :ts GROUP BY decision"
        )
        res_dec = await session.execute(q_decisions, {"ts": datetime(2025, 12, 23, tzinfo=timezone.utc)})
        for row in res_dec:
            logger.info(f"Strategy Decisions: {row[1]} = {row[0]}")

        # 4. Check Journals
        q_journal = text("SELECT count(*) FROM trade_journal_entries WHERE created_at_utc > :ts")
        res_journal = await session.execute(q_journal, {"ts": datetime(2025, 12, 23, tzinfo=timezone.utc)})
        logger.info(f"Trade Journal Entries: {res_journal.scalar()}")


if __name__ == "__main__":
    asyncio.run(verify())
