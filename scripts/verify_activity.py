import asyncio
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from loguru import logger
from sqlalchemy import text

# Load env for host access to DB
load_dotenv()
if os.getenv("DB_URL"):
    os.environ["DB_URL"] = os.getenv("DB_URL").replace(":5432", ":5440").replace("@timescaledb", "@localhost")

from orion.storage.db import async_session_factory, init_db

# Configure loguru for nice UX
logger.remove()
logger.add(sys.stderr, format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>", level="INFO")


async def verify():
    logger.info("🎨 Palette: Starting Activity Verification Checks...")
    check_date = datetime(2025, 12, 23, tzinfo=timezone.utc)
    logger.info(f"📅 Checking activity since: <bold>{check_date.isoformat()}</bold>")

    await init_db()

    async with async_session_factory() as session:
        # 1. Check Bronze Events (Backfilled?)
        logger.info("\n🔍 <cyan>1. Checking Bronze Events...</cyan>")
        q_bronze = text("SELECT source, count(*) FROM bronze_events WHERE event_ts_utc > :ts GROUP BY source")
        res_bronze = await session.execute(q_bronze, {"ts": check_date})

        total = 0
        rows = list(res_bronze)
        if rows:
            for row in rows:
                logger.info(f"   • {row[0]}: <bold>{row[1]}</bold>")
                total += row[1]
        else:
            logger.info("   ⚠️  No bronze events found.")

        logger.info(f"   ✨ Total Bronze Events: <bold>{total}</bold>")

        # 2. Check Candidate Trades (Gold)
        logger.info("\n🔍 <cyan>2. Checking Candidate Trades...</cyan>")
        q_candidates = text("SELECT count(*) FROM candidate_trades WHERE created_at_utc > :ts")
        res_cand = await session.execute(q_candidates, {"ts": check_date})
        count = res_cand.scalar()
        logger.info(f"   🏆 Candidate Trades: <bold>{count}</bold>")

        # 3. Check Strategy Decisions
        logger.info("\n🔍 <cyan>3. Checking Strategy Decisions...</cyan>")
        q_decisions = text(
            "SELECT count(*), decision FROM strategy_decisions WHERE timestamp_utc > :ts GROUP BY decision"
        )
        res_dec = await session.execute(q_decisions, {"ts": check_date})
        rows_dec = list(res_dec)
        if rows_dec:
            for row in rows_dec:
                logger.info(f"   🤖 {row[1]}: <bold>{row[0]}</bold>")
        else:
            logger.info("   ⚠️  No strategy decisions found.")

        # 4. Check Journals
        logger.info("\n🔍 <cyan>4. Checking Trade Journals...</cyan>")
        q_journal = text("SELECT count(*) FROM trade_journal_entries WHERE created_at_utc > :ts")
        res_journal = await session.execute(q_journal, {"ts": check_date})
        j_count = res_journal.scalar()
        logger.info(f"   📔 Journal Entries: <bold>{j_count}</bold>")

    logger.info("\n✅ <green>Verification Complete.</green>")


if __name__ == "__main__":
    asyncio.run(verify())
