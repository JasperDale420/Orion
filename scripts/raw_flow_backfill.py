#!/usr/bin/env python3
"""
Backfill UW FLOW data with RAW payloads preserved.
This ensures bronze has the original UW fields (has_sweep, total_ask_side_prem, etc.)
so the fixed normalizer can correctly derive aggressor when persisting to silver.
"""
import asyncio
import hashlib
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from datetime import time as dt_time
from typing import List

import requests
from dotenv import load_dotenv
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

load_dotenv()

# Fix DB URL for local access
DB_URL = os.getenv("DB_URL", "")
if ":5432" in DB_URL:
    DB_URL = DB_URL.replace(":5432", ":5440").replace("@timescaledb", "@localhost")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("raw_flow_backfill")

# UW API config
UW_API_KEY = os.getenv("UW_API_KEY")
UW_BASE_URL = os.getenv("UW_BASE_URL", "https://api.unusualwhales.com/api")


@retry(
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=30),
)
def fetch_flow_page(older_than: str, limit: int = 500) -> List[dict]:
    """Fetch a page of flow alerts from UW API."""
    url = f"{UW_BASE_URL}/option-trades/flow-alerts"
    params = {"limit": limit, "older_than": older_than}

    time.sleep(0.6)  # Rate limit: 120 req/min

    resp = requests.get(url, params=params, headers={"Authorization": f"Bearer {UW_API_KEY}"}, timeout=30)

    daily_count = resp.headers.get("x-uw-daily-req-count", "N/A")
    logger.info(f"UW API: {daily_count} / 15000 daily requests")

    resp.raise_for_status()
    data = resp.json()

    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data if isinstance(data, list) else []


def fetch_day(target_date: date) -> List[dict]:
    """Fetch all flow alerts for a given day by paging backwards."""
    all_events = []
    seen_ids = set()

    cursor = datetime.combine(target_date + timedelta(days=1), dt_time(0, 0, tzinfo=timezone.utc)).isoformat()

    while True:
        logger.info(f"Fetching older_than={cursor}...")
        batch = fetch_flow_page(older_than=cursor)

        if not batch:
            break

        def get_ts(item):
            return str(item.get("timestamp") or item.get("created_at") or "")

        oldest_ts = min((get_ts(it) for it in batch if get_ts(it)), default=None)
        if not oldest_ts:
            break

        # Only keep events from target day
        new_count = 0
        for item in batch:
            ts = get_ts(item)
            if not ts or ts[:10] != target_date.isoformat():
                continue

            item_id = str(item.get("id") or "")
            if item_id and item_id in seen_ids:
                continue
            if item_id:
                seen_ids.add(item_id)

            all_events.append(item)
            new_count += 1

        logger.info(f"Batch: {len(batch)} items, +{new_count} in-day new, total={len(all_events)}")

        if oldest_ts[:10] < target_date.isoformat():
            break
        if oldest_ts == cursor:
            logger.warning("Cursor didn't advance, stopping")
            break

        cursor = oldest_ts

    return all_events


def generate_event_id(raw: dict) -> str:
    """Generate deterministic event ID."""
    if "id" in raw:
        return hashlib.sha256(f"UW_FLOW_{raw['id']}".encode()).hexdigest()
    else:
        stable = f"{raw.get('ticker')}_{raw.get('created_at')}_{raw.get('total_premium')}"
        return hashlib.sha256(f"UW_FLOW_HASH_{stable}".encode()).hexdigest()


async def backfill_days(days: int = 14):
    """Backfill UW flow data for the past N days."""
    engine = create_async_engine(DB_URL)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Calculate date range
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days)
    dates = [start_date + timedelta(days=i) for i in range(days + 1) if (start_date + timedelta(days=i)) <= end_date]

    logger.info(f"Backfilling {len(dates)} days: {dates[0]} to {dates[-1]}")

    for target_date in dates:
        logger.info(f"=== Processing {target_date} ===")

        # Fetch raw events
        raw_events = await asyncio.to_thread(fetch_day, target_date)
        logger.info(f"Fetched {len(raw_events)} raw events for {target_date}")

        if not raw_events:
            continue

        # Prepare bronze records with RAW payload (no normalization!)
        values = []
        seen_ids = set()

        for raw in raw_events:
            try:
                event_id = generate_event_id(raw)
                if event_id in seen_ids:
                    continue
                seen_ids.add(event_id)

                ts_str = raw.get("timestamp") or raw.get("created_at")
                ticker = raw.get("ticker") or raw.get("underlying") or raw.get("symbol")

                if not ticker:
                    continue

                values.append(
                    {
                        "event_id": event_id,
                        "source": "UW",
                        "source_event_id": str(raw.get("id")) if raw.get("id") else None,
                        "event_type": "UW_FLOW",
                        "ticker": ticker,
                        "trading_date": target_date,
                        "session": "REG",
                        "schema_version": "v1",
                        "event_ts_utc": datetime.fromisoformat(ts_str.replace("Z", "+00:00")),
                        "received_ts_utc": datetime.now(timezone.utc),
                        "payload": raw,  # RAW payload - not normalized!
                        "ingest": {"connector": "raw_backfill", "run_id": f"raw_bf_{target_date}"},
                    }
                )
            except Exception as e:
                logger.warning(f"Skip event: {e}")

        logger.info(f"Prepared {len(values)} bronze records (with raw payload)")

        # Insert to bronze
        if values:
            async with AsyncSessionLocal() as session:
                from orion.storage.models import BronzeEvent

                # Batch insert with ON CONFLICT DO NOTHING
                for i in range(0, len(values), 1000):
                    batch = values[i : i + 1000]
                    stmt = insert(BronzeEvent).values(batch)
                    stmt = stmt.on_conflict_do_nothing(index_elements=["event_id"])
                    await session.execute(stmt)

                await session.commit()
                logger.info(f"Inserted bronze events for {target_date}")

        # Now persist to silver using the fixed normalizer
        async with AsyncSessionLocal() as session:
            from orion.processing.normalizer import NormalizationEngine
            from orion.processing.persistence import persist_silver_from_bronze
            from orion.storage.models import BronzeEvent
            from sqlalchemy import select

            # Fetch the bronze events we just inserted
            result = await session.execute(
                select(BronzeEvent).where(BronzeEvent.event_type == "UW_FLOW", BronzeEvent.trading_date == target_date)
            )
            bronze_events = list(result.scalars().all())

            # Normalize payload for silver persistence (the normalizer reads raw fields)
            for e in bronze_events:
                if e.payload:
                    e.payload = NormalizationEngine.normalize_event("UW", "UW_FLOW", e.payload)

            # Persist to silver
            await persist_silver_from_bronze(session, bronze_events)
            await session.commit()
            logger.info(f"Persisted {len(bronze_events)} events to silver for {target_date}")

    logger.info("=== Backfill complete ===")


if __name__ == "__main__":
    asyncio.run(backfill_days(14))
