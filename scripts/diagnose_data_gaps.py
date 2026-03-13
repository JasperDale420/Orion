import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv
from sqlalchemy import text

from orion.core.logging_config import setup_logging

# Load env for host access to DB
load_dotenv()
if os.getenv("DB_URL"):
    os.environ["DB_URL"] = os.getenv("DB_URL").replace(":5432", ":5440").replace("@timescaledb", "@localhost")

setup_logging()
logger = logging.getLogger("diagnosis")


async def try_connect(url):
    """Attempt to create an async engine with the given URL."""
    from sqlalchemy.ext.asyncio import create_async_engine

    try:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        return engine
    except Exception as e:
        logger.warning(f"Connection failed for {url.split('@')[-1]}: {e}")
        return None


async def diagnose():
    # Try default URL first
    default_url = os.getenv("DB_URL")

    # Fallback URLs
    urls_to_try = [
        default_url,
        "postgresql+asyncpg://postgres:password@localhost:5440/orion_db",
        "postgresql+asyncpg://postgres:postgres@localhost:5440/orion_db",
        "postgresql+asyncpg://postgres:orion_password@localhost:5440/orion_db",
        "postgresql+asyncpg://orion:orion_password@localhost:5440/postgres",  # maybe db name differ?
    ]

    engine = None
    for url in urls_to_try:
        if not url:
            continue
        logger.info("Trying connection...")
        # Dirty hack to override global if needed, but we used init_db which uses global settings.
        # Instead, let's just manually create engine and session for this script.
        try:
            e = await try_connect(url)
            if e:
                engine = e
                logger.info("Connected successfully!")
                break
        except Exception:
            pass

    if not engine:
        logger.error("Could not connect to DB with any credentials.")
        return

    # Use the working engine
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import sessionmaker

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    end_date = datetime.now(UTC).date()
    start_date = end_date - timedelta(days=7)

    logger.info(f"Diagnosing data gaps from {start_date} to {end_date}...\n")

    async with async_session() as session:
        # 1. Daily Bronze Counts by Source/Type
        logger.info("--- Daily Bronze Event Counts ---")
        q_bronze = text(
            """
            SELECT
                date_trunc('day', event_ts_utc) as day,
                source,
                event_type,
                count(*)
            FROM bronze_events
            WHERE event_ts_utc >= :start_ts
            GROUP BY 1, 2, 3
            ORDER BY 1 DESC, 2, 3
        """
        )

        try:
            res_bronze = await session.execute(q_bronze, {"start_ts": start_date})
            rows = res_bronze.fetchall()

            if not rows:
                logger.warning("No Bronze Events found in the last 7 days.")
            else:
                # Group by day for cleaner output
                daily_data = {}
                for day, source, etype, count in rows:
                    if day is None:
                        continue
                    d_str = day.date().isoformat()
                    if d_str not in daily_data:
                        daily_data[d_str] = []
                    daily_data[d_str].append(f"{source} ({etype}): {count}")

                for d in sorted(daily_data.keys(), reverse=True):
                    logger.info(f"Date: {d}")
                    for item in daily_data[d]:
                        logger.info(f"  - {item}")
                    logger.info("")
        except Exception as e:
            logger.error(f"Query failed: {e}")


if __name__ == "__main__":
    asyncio.run(diagnose())
