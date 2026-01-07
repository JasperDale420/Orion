import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import select

from orion.storage.db import async_session_factory, init_db
from orion.storage.models import BronzeEvent

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("orion.recon.bar_scan")


async def scan_ticker_gaps(session: Any, ticker: str, start_ts: datetime, end_ts: datetime) -> None:
    """
    Checks for missing 1m bars for a ticker between start_ts and end_ts.
    PRD 9.2: Bar gap scan.
    """
    # 1. Fetch all bar timestamps
    # Assuming 'ALPACA_BAR_1M' events
    stmt = (
        select(BronzeEvent.event_ts_utc)
        .where(BronzeEvent.event_type == "ALPACA_BAR_1M")
        .where(BronzeEvent.ticker == ticker)
        .where(BronzeEvent.event_ts_utc >= start_ts)
        .where(BronzeEvent.event_ts_utc <= end_ts)
        .order_by(BronzeEvent.event_ts_utc.asc())
    )

    result = await session.execute(stmt)
    timestamps = result.scalars().all()

    if not timestamps:
        logger.warning(f"{ticker}: No bars found in range {start_ts} to {end_ts}")
        return

    ts_set = {ts.replace(second=0, microsecond=0) for ts in timestamps}

    # Generate expected timestamps (very naive: assuming continuous market hours for this check)
    # In production, use exchange calendar to skip disconnects.
    # For v1 audit compliance: We scan the range provided assuming it IS market session.

    current = start_ts.replace(second=0, microsecond=0)
    end = end_ts.replace(second=0, microsecond=0)

    expected_count = 0
    missing_count = 0

    while current <= end:
        expected_count += 1
        if current not in ts_set:
            missing_count += 1
            # logger.debug(f"{ticker}: Missing bar at {current}")
        current += timedelta(minutes=1)

    if missing_count > 0:
        pct = (missing_count / expected_count) * 100
        logger.error(f"GAP DETECTED: {ticker} missing {missing_count}/{expected_count} bars ({pct:.1f}%)")
        # PRD 9.1: Missing bars > 2 min for > 10% universe.
        # This script just Logs.
    else:
        logger.info(f"{ticker}: Clean. {expected_count} bars verified.")


async def main() -> None:
    await init_db()

    # Scan last 24h? Or just 'Today'?
    # Let's scan 'Today' from 09:30 to now (if active) or 16:00.
    now = datetime.utcnow()
    # Simple hardcoded window for demo
    start_window = now - timedelta(hours=1)
    end_window = now

    logger.info(f"Starting Bar Gap Scan: {start_window} -> {end_window}")

    async with async_session_factory() as session:
        # Get active tickers from DB (distinct tickets in window?)
        stmt = select(BronzeEvent.ticker).where(BronzeEvent.event_ts_utc >= start_window).distinct()
        result = await session.execute(stmt)
        tickers = result.scalars().all()

        if not tickers:
            logger.warning("No active tickers found in window.")
            return

        logger.info(f"Scanning {len(tickers)} tickers...")

        for ticker in tickers:
            if ticker:
                await scan_ticker_gaps(session, ticker, start_window, end_window)


if __name__ == "__main__":
    asyncio.run(main())
