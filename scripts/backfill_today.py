import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from datetime import time as dt_time
from typing import List

from dotenv import load_dotenv

# Load env
load_dotenv()
os.environ["DB_URL"] = os.getenv("DB_URL").replace(":5432", ":5440").replace("@timescaledb", "@localhost")

from orion.config import system_settings
from orion.connectors.alpaca_market_connector import AlpacaMarketConnector
from orion.connectors.uw_flow_connector import UWFlowConnector
from orion.core.universe_manager import UniverseManager
from orion.processing.ingest_pipeline import ingest_bronze_events
from orion.processing.persistence import persist_silver_from_bronze
from orion.shared.utils import parse_timestamptz
from orion.storage.db import async_session_factory, init_db
from orion.storage.models import BronzeEvent

# Setup Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backfill")

RUN_ID = f"backfill_{datetime.now().strftime('%Y%m%d')}"


async def backfill_uw(date_target: datetime):
    logger.info(f"Backfilling UW data for {date_target.date()}...")
    connector = UWFlowConnector(api_key=system_settings.uw_api_key)

    # Cool down
    time.sleep(5)
    system_settings.uw_fetch_limit = 1000

    # Define range (full UTC day)
    start_ts = datetime.combine(date_target.date(), dt_time.min).replace(tzinfo=timezone.utc)
    end_ts = datetime.combine(date_target.date(), dt_time.max).replace(tzinfo=timezone.utc)

    # Fetch Raw
    raw_events = await connector.fetch_raw_events(start_ts, end_ts)
    logger.info(f"Fetched {len(raw_events)} raw UW events.")

    bronze_events = []
    seen_event_ids = set()

    # Convert to Bronze (logic adapted from UWFlowConnector.poll)
    for raw in raw_events:
        try:
            # Logic copied from UWFlowConnector.poll
            ts_str = raw.get("timestamp") or raw.get("created_at")
            event_ts = parse_timestamptz(ts_str, strict=True)

            event_id = connector._generate_event_id(raw)
            if event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)

            # Normalize Payload
            if "premium" not in raw and "total_premium" in raw:
                raw["premium"] = raw["total_premium"]
            if "put_call" not in raw and "type" in raw:
                t = raw["type"].upper()
                raw["put_call"] = "C" if t == "CALL" else ("P" if t == "PUT" else t[:1])

            bronze = BronzeEvent(
                event_id=event_id,
                source="UW",
                source_event_id=str(raw.get("id")) if raw.get("id") else None,
                event_type="UW_FLOW",
                event_ts_utc=event_ts,
                received_ts_utc=datetime.now(timezone.utc),
                payload=raw,
                session="REG",
            )
            bronze_events.append(bronze)
        except Exception as e:
            logger.warning(f"Skipping event: {e}")

    return bronze_events


async def backfill_alpaca(date_target: datetime, tickers: List[str]):
    logger.info(f"Backfilling Alpaca bars for {len(tickers)} tickers on {date_target.date()}...")
    connector = AlpacaMarketConnector(
        api_key=system_settings.alpaca_api_key, secret_key=system_settings.alpaca_secret_key
    )

    # Market Hours
    start_ts = datetime.combine(date_target.date(), dt_time(14, 30)).replace(tzinfo=timezone.utc)  # 09:30 ET
    end_ts = datetime.combine(date_target.date(), dt_time(21, 0)).replace(tzinfo=timezone.utc)  # 16:00 ET

    # Use fetch_bars
    events = connector.fetch_bars(tickers, start_ts, end_ts)
    logger.info(f"Fetched {len(events)} Alpaca bars.")
    return events


async def main():
    target_date = datetime(2025, 12, 23, tzinfo=timezone.utc)

    await init_db()

    # 1. Universe
    universe = UniverseManager()
    await universe.hydrate_from_db()
    active_tickers = universe.get_active_universe()
    logger.info(f"Target Universe: {len(active_tickers)} tickers")

    all_events = []

    # 2. UW
    try:
        uw_events = await backfill_uw(target_date)
        all_events.extend(uw_events)
    except Exception as e:
        logger.error(f"UW Backfill Failed: {e}")

    # 3. Alpaca
    if active_tickers:
        alpaca_events = await backfill_alpaca(target_date, active_tickers)
        all_events.extend(alpaca_events)

    logger.info(f"Total Bronze Events to Ingest: {len(all_events)}")

    # 4. Ingest & Persist
    if all_events:
        async with async_session_factory() as session:
            # Ingest Pipeline (Dedup/Normalize)
            unique_events = await ingest_bronze_events(session, all_events, run_id=RUN_ID, trace_id="manual_backfill")
            logger.info(f"Unique Events after Dedup: {len(unique_events)}")

            # Persist Bronze
            from orion.processing.persistence import persist_bronze_events

            await persist_bronze_events(session, unique_events)

            # Persist Silver
            await persist_silver_from_bronze(session, unique_events)

            await session.commit()
            logger.info("Persistence Complete.")


if __name__ == "__main__":
    asyncio.run(main())
