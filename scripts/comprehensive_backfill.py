import argparse
import asyncio
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from datetime import time as dt_time
from typing import List

import requests
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

# --- Configuration & Setup ---
load_dotenv()
# Robust DB URL handling
DB_URL = os.getenv("DB_URL", "")
if ":5432" in DB_URL:
    DB_URL = DB_URL.replace(":5432", ":5440").replace("@timescaledb", "@localhost")

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("backfill")

# --- Connectors & Models ---
# We import these AFTER setting env vars if possible, or just rely on them reading env
from orion.config import system_settings
from orion.connectors.alpaca_market_connector import AlpacaMarketConnector
from orion.connectors.uw_darkpool_connector import UWDarkPoolConnector
from orion.connectors.uw_flow_connector import UWFlowConnector
from orion.processing.ingest_pipeline import ingest_bronze_events
from orion.processing.persistence import persist_bronze_events, persist_silver_from_bronze
from orion.shared.utils import parse_timestamptz
from orion.storage.models import BronzeEvent

RUN_ID = f"comprehensive_backfill_{datetime.now().strftime('%Y%m%d%H%M')}"

# --- Robust Connector Subclasses ---


class RobustUWConnector(UWFlowConnector):
    """Overrides fetch logic to handle strict pagination for Flow/Alerts."""

    def fetch_all_pages(self, target_date: date) -> list:
        all_events = []
        seen_ids = set()
        offset = 0
        limit = 1000

        while True:
            logger.info(f"[UW Flow] Fetching offset={offset} limit={limit}...")
            batch = self._fetch_page(target_date, offset, limit)

            if not batch:
                logger.info("[UW Flow] No more events found.")
                break

            # De-duplicate within batch to check for infinite loops
            new_in_batch = 0
            for item in batch:
                eid = str(item.get("id"))
                if eid not in seen_ids:
                    seen_ids.add(eid)
                    new_in_batch += 1

            if new_in_batch == 0 and len(batch) > 0:
                logger.warning("[UW Flow] Batch contains only duplicate IDs. Stopping.")
                break

            all_events.extend(batch)
            logger.info(f"[UW Flow] Got {len(batch)} events ({new_in_batch} new). Total: {len(all_events)}")

            if len(batch) < limit:
                break

            offset += limit
            time.sleep(0.5)  # Rate limit niceness

        return all_events

    @retry(
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        stop=stop_after_attempt(10),
        wait=wait_exponential(multiplier=2, min=5, max=60),
    )
    def _fetch_page(self, target_date: date, offset: int, limit: int) -> list:
        url = f"{self.base_url}/option-trades/flow-alerts"
        params = {"date": target_date.strftime("%Y-%m-%d"), "limit": limit, "offset": offset}
        try:
            # Strict Rate Limiting: 120 req/min = 2 req/sec => 0.5s interval
            # Using 0.6s to be safe
            time.sleep(0.6)

            response = self.session.get(url, params=params, timeout=30)

            # Log Rate Limit Headers at INFO so user can see them
            daily_count = response.headers.get("x-uw-daily-req-count", "N/A")
            limit_total = response.headers.get("x-uw-token-req-limit", "N/A")
            if daily_count != "N/A":
                logger.info(f"[UW Headers] Daily Count: {daily_count} / {limit_total}")

            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and "data" in data:
                return data["data"]
            return data if isinstance(data, list) else []
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                d_c = e.response.headers.get("x-uw-daily-req-count", "N/A")
                l_t = e.response.headers.get("x-uw-token-req-limit", "N/A")
                logger.warning(f"[UW Flow] 429 Rate Limit on offset {offset}! Usage: {d_c}/{l_t}. Backing off...")
                # The @retry decorator handles the wait
                raise e
            raise e


# --- Helpers ---


async def get_db_url_and_engine():
    """Try to connect to DB with various credentials, return working URL and engine."""

    # Prioritize fallback credentials if default is known to fail
    # Note: If DB_URL already has correct credentials from previous fix, it should work.
    urls = [
        os.getenv("DB_URL"),
        "postgresql+asyncpg://postgres:password@localhost:5440/orion_db",
        "postgresql+asyncpg://postgres:postgres@localhost:5440/orion_db",
        "postgresql+asyncpg://orion:orion_password@localhost:5440/orion_db",
    ]

    for url in urls:
        if not url:
            continue
        try:
            engine = create_async_engine(url)
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info(f"Connected to DB via: {url.split('@')[-1]}")
            return url, engine
        except Exception:
            # logger.warning(f"Failed {url}: {e}")
            pass
    return None, None


async def backfill_day(session, date_target: date, active_tickers: List[str]):
    logger.info(f"=== Starting Backfill for {date_target} ===")

    events_to_ingest = []

    # 1. UW Flow
    try:
        uw_conn = RobustUWConnector(api_key=system_settings.uw_api_key)
        raw_rows = await asyncio.to_thread(uw_conn.fetch_all_pages, date_target)

        for raw in raw_rows:
            try:
                # Normalization
                if "premium" not in raw and "total_premium" in raw:
                    raw["premium"] = raw["total_premium"]
                if "put_call" not in raw and "type" in raw:
                    t = raw["type"].upper()
                    raw["put_call"] = "C" if t == "CALL" else ("P" if t == "PUT" else t[:1])

                eid = uw_conn._generate_event_id(raw)
                ts_str = raw.get("timestamp") or raw.get("created_at")

                events_to_ingest.append(
                    BronzeEvent(
                        event_id=eid,
                        source="UW",
                        source_event_id=str(raw.get("id")) if raw.get("id") else None,
                        event_type="UW_FLOW",
                        event_ts_utc=parse_timestamptz(ts_str, strict=True),
                        received_ts_utc=datetime.now(timezone.utc),
                        payload=raw,
                        session="REG",
                    )
                )
            except Exception:
                pass

        logger.info(f"[UW Flow] Collected {len(raw_rows)} raw events -> {len(events_to_ingest)} bronze candidates")
    except Exception as e:
        logger.error(f"[UW Flow] Failed: {e}")

    # 2. UW Dark Pool
    try:
        current_len = len(events_to_ingest)
        # Fix: use os.getenv instead of system_settings.uw_base_url if missing
        uw_base = getattr(system_settings, "uw_base_url", os.getenv("UW_BASE_URL", "https://api.unusualwhales.com/api"))
        dp_conn = UWDarkPoolConnector(api_key=system_settings.uw_api_key, base_url=uw_base)

        # Note: UWDarkPoolConnector doesn't expose a clean sync fetch by date method that returns raw list easily
        # We will use the private method or reimplement simple fetch
        # Re-implementing ensure we control retries
        raw_dp = await dp_conn._fetch_raw_for_date(date_target.strftime("%Y-%m-%d"))

        for raw in raw_dp:
            try:
                eid = dp_conn._generate_event_id(raw)
                ts_str = raw.get("executed_at") or raw.get("timestamp") or raw.get("date")
                events_to_ingest.append(
                    BronzeEvent(
                        event_id=eid,
                        source="UW",
                        event_type="UW_DARKPOOL",
                        event_ts_utc=parse_timestamptz(ts_str, strict=True),
                        received_ts_utc=datetime.now(timezone.utc),
                        payload=raw,
                        session="REG",
                    )
                )
            except Exception:
                pass

        logger.info(f"[UW DarkPool] Collected {len(raw_dp)} raw events -> {len(events_to_ingest) - current_len} added")
    except Exception as e:
        logger.error(f"[UW DarkPool] Failed: {e}")

    # 3. Alpaca Bars
    if active_tickers:
        try:
            current_len = len(events_to_ingest)
            alpaca_conn = AlpacaMarketConnector(
                api_key=system_settings.alpaca_api_key,
                secret_key=system_settings.alpaca_secret_key,
                paper=system_settings.alpaca_paper,
            )

            start_ts = datetime.combine(date_target, dt_time(14, 30, tzinfo=timezone.utc))  # 09:30 ET
            end_ts = datetime.combine(date_target, dt_time(21, 0, tzinfo=timezone.utc))  # 16:00 ET

            # Chunk tickers to avoid size limits
            chunk_size = 50
            total_bars = 0
            for i in range(0, len(active_tickers), chunk_size):
                chunk = active_tickers[i : i + chunk_size]
                if not chunk:
                    continue
                bars = alpaca_conn.fetch_bars(chunk, start_ts, end_ts)
                events_to_ingest.extend(bars)
                total_bars += len(bars)
                time.sleep(0.2)

            logger.info(f"[Alpaca] Collected {total_bars} bars for {len(active_tickers)} tickers")
        except Exception as e:
            logger.error(f"[Alpaca] Failed: {e}")

    # 4. Ingest & Persist
    if events_to_ingest:
        logger.info(f"Ingesting {len(events_to_ingest)} total events...")
        # Ingest (Dedup)
        unique = await ingest_bronze_events(session, events_to_ingest, run_id=RUN_ID, trace_id=f"bf_{date_target}")
        logger.info(f"Unique Events: {len(unique)}")

        # Persist
        await persist_bronze_events(session, unique)
        await persist_silver_from_bronze(session, unique)
        await session.commit()
        logger.info("Persisted successfully.")
    else:
        logger.warning("No events to ingest for this day.")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="Number of days to backfill")
    parser.add_argument("--start-date", type=str, help="YYYY-MM-DD start date (optional)")
    args = parser.parse_args()

    # 1. Establish DB Connection
    working_url, engine = await get_db_url_and_engine()
    if not engine:
        logger.error("Failed to connect to DB. Exiting.")
        return

    # Patch Environment so downstream components (if any) see the working URL
    os.environ["DB_URL"] = working_url

    # Hack to inject engine into global session factory if needed,
    # but we can just use this engine for a local sessionmaker
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import sessionmaker

    LocalSession = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # 2. Hydrate Active Universe
    # Logic adapted from UniverseManager.hydrate_from_db + Static Config
    from orion.config import STATIC_WATCHLIST

    active_tickers = set(STATIC_WATCHLIST)
    logger.info(f"Static Watchlist: {len(active_tickers)} tickers")

    async with LocalSession() as session:
        # Check for active alerts (future expiry) in silver_uw_alerts
        # We use raw SQL to avoid importing SilverUWAlert if not strictly needed,
        # but importing it is cleaner if available.
        # Let's try raw SQL on 'silver_uw_alerts' table
        try:
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            q = text("SELECT DISTINCT ticker FROM silver_uw_alerts WHERE expiry >= :today")
            res = await session.execute(q, {"today": today_str})
            db_tickers = {r[0] for r in res if r[0]}
            logger.info(f"Active Contexts from DB: {len(db_tickers)} tickers")
            active_tickers.update(db_tickers)
        except Exception as e:
            logger.warning(f"Failed to fetch active contexts from DB (skipping dynamic universe): {e}")

    tickers_list = list(active_tickers)
    logger.info(f"Total Target Universe: {len(tickers_list)} tickers")

    # 3. Determine Dates
    dates = []
    if args.start_date:
        start = datetime.strptime(args.start_date, "%Y-%m-%d").date()
        for i in range(args.days):
            dates.append(start + timedelta(days=i))
    else:
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=args.days)
        for i in range(args.days + 1):
            d = start + timedelta(days=i)
            if d <= end:
                dates.append(d)

    logger.info(f"Backfilling Dates: {dates}")

    # 4. Execute Backfill
    async with LocalSession() as session:
        for d in dates:
            await backfill_day(session, d, tickers_list)


if __name__ == "__main__":
    asyncio.run(main())
