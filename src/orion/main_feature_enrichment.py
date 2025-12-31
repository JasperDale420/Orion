"""
UW Feature Enrichment Service.

Periodically fetches GEX, Market Tide, Max Pain, IV Rank for tracked tickers.
Runs as a background service to populate feature tables for ML.
"""

import asyncio
import os
import signal
from datetime import datetime, timezone
from typing import Any, List

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from orion.connectors.uw_greek_exposure_connector import UWGreekExposureConnector
from orion.connectors.uw_iv_rank_connector import UWIVRankConnector
from orion.connectors.uw_market_tide_connector import UWMarketTideConnector
from orion.connectors.uw_max_pain_connector import UWMaxPainConnector
from orion.shared.db_utils import db_query
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db

logger = setup_struct_logger("orion.feature_enrichment")

# Poll intervals
MARKET_TIDE_INTERVAL = 60  # Every minute
GREEK_EXPOSURE_INTERVAL = 300  # Every 5 minutes
MAX_PAIN_INTERVAL = 3600  # Every hour
IV_RANK_INTERVAL = 900  # Every 15 minutes


async def get_active_tickers(limit: int = 20) -> List[str]:
    """Get tickers with recent flow activity."""

    async def query(session: Any) -> List[str]:
        stmt = text(
            """
            SELECT ticker
            FROM silver_uw_flow
            WHERE flow_ts_utc > NOW() - INTERVAL '1 day'
            AND ticker IS NOT NULL
            GROUP BY ticker
            ORDER BY COUNT(*) DESC
            LIMIT :limit
        """
        )
        result = await session.execute(stmt, {"limit": limit})
        return [row[0] for row in result.fetchall()]

    try:
        return await db_query(query)
    except Exception:
        # Fallback to common tickers
        return ["SPY", "QQQ", "TSLA", "NVDA", "AAPL", "AMD", "META", "AMZN", "GOOG", "MSFT"]


async def run_feature_loop(shutdown_event: asyncio.Event) -> None:
    """Main feature enrichment loop."""
    await init_db()

    api_key = os.environ.get("UW_API_KEY")
    if not api_key:
        logger.error("UW_API_KEY not set")
        return

    # Initialize connectors
    greek_connector = UWGreekExposureConnector(api_key)
    tide_connector = UWMarketTideConnector(api_key)
    max_pain_connector = UWMaxPainConnector(api_key)
    iv_connector = UWIVRankConnector(api_key)

    last_tide = datetime.min.replace(tzinfo=timezone.utc)
    last_greek = datetime.min.replace(tzinfo=timezone.utc)
    last_max_pain = datetime.min.replace(tzinfo=timezone.utc)
    last_iv = datetime.min.replace(tzinfo=timezone.utc)

    logger.info("Feature Enrichment Service started")

    while not shutdown_event.is_set():
        try:
            now = datetime.now(timezone.utc)
            tickers = await get_active_tickers()

            # Market Tide - every minute
            if (now - last_tide).total_seconds() >= MARKET_TIDE_INTERVAL:
                count = await tide_connector.fetch_and_store()
                logger.info(f"Market Tide: stored {count} ticks")
                last_tide = now

            # Greek Exposure - every 5 minutes
            if (now - last_greek).total_seconds() >= GREEK_EXPOSURE_INTERVAL:
                count = await greek_connector.fetch_and_store(tickers)
                logger.info(f"Greek Exposure: stored {count} records for {len(tickers)} tickers")
                last_greek = now

            # Max Pain - every hour
            if (now - last_max_pain).total_seconds() >= MAX_PAIN_INTERVAL:
                count = await max_pain_connector.fetch_and_store(tickers)
                logger.info(f"Max Pain: stored {count} records")
                last_max_pain = now

            # IV Rank - every 15 minutes
            if (now - last_iv).total_seconds() >= IV_RANK_INTERVAL:
                count = await iv_connector.fetch_and_store(tickers)
                logger.info(f"IV Rank: stored {count} records")
                last_iv = now

        except Exception as e:
            logger.error(f"Feature enrichment error: {e}", exc_info=True)

        # Wait before next iteration
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=30)
            break
        except asyncio.TimeoutError:
            pass

    logger.info("Feature Enrichment Service stopped")


async def main() -> None:
    """Main entry point."""
    shutdown_event = asyncio.Event()
    loop = asyncio.get_event_loop()

    def handle_signal(sig: int) -> None:
        logger.info(f"Received signal {sig}. Shutting down...")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: handle_signal(s))

    await run_feature_loop(shutdown_event)


if __name__ == "__main__":
    asyncio.run(main())
