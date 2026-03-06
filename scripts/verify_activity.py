import asyncio
import os
from datetime import UTC, datetime

import structlog
from dotenv import load_dotenv
from sqlalchemy import text

logger = structlog.get_logger()

# Load env for host access to DB
load_dotenv()
if os.getenv("DB_URL"):
    os.environ["DB_URL"] = os.getenv("DB_URL").replace(":5432", ":5440").replace("@timescaledb", "@localhost")

from orion.storage.db import async_session_factory, init_db


async def verify():
    logger.info("Starting Activity Verification Checks")
    check_date = datetime(2025, 12, 23, tzinfo=UTC)
    logger.info(f"Checking activity since: {check_date.isoformat()}")

    await init_db()

    async with async_session_factory() as session:
        # 1. Check Bronze Events (Backfilled?)
        logger.info("1. Checking Bronze Events...")
        q_bronze = text("SELECT source, count(*) FROM bronze_events WHERE event_ts_utc > :ts GROUP BY source")
        res_bronze = await session.execute(q_bronze, {"ts": check_date})

        total = 0
        rows = list(res_bronze)
        if rows:
            for row in rows:
                logger.info(f"   {row[0]}: {row[1]}")
                total += row[1]
        else:
            logger.info("   No bronze events found.")

        logger.info(f"   Total Bronze Events: {total}")

        # 2. Check Candidate Trades (Gold)
        logger.info("2. Checking Candidate Trades...")
        q_candidates = text("SELECT count(*) FROM candidate_trades WHERE created_at_utc > :ts")
        res_cand = await session.execute(q_candidates, {"ts": check_date})
        count = res_cand.scalar()
        logger.info(f"   Candidate Trades: {count}")

        # 3. Check Strategy Decisions
        logger.info("3. Checking Strategy Decisions...")
        q_decisions = text(
            "SELECT count(*), decision FROM strategy_decisions WHERE timestamp_utc > :ts GROUP BY decision"
        )
        res_dec = await session.execute(q_decisions, {"ts": check_date})
        rows_dec = list(res_dec)
        if rows_dec:
            for row in rows_dec:
                logger.info(f"   {row[1]}: {row[0]}")
        else:
            logger.info("   No strategy decisions found.")

        # 4. Check Journals
        logger.info("4. Checking Trade Journals...")
        q_journal = text("SELECT count(*) FROM trade_journal_entries WHERE created_at_utc > :ts")
        res_journal = await session.execute(q_journal, {"ts": check_date})
        j_count = res_journal.scalar()
        logger.info(f"   Journal Entries: {j_count}")

    logger.info("Verification Complete.")


if __name__ == "__main__":
    asyncio.run(verify())
