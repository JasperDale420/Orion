import asyncio
import logging
import os
import time
from datetime import date, datetime, timezone

import requests
from dotenv import load_dotenv
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

# Load env and force DB URL for local host access
load_dotenv()
db_url = os.getenv("DB_URL", "")
# Always point to 5440 for Docker's exposed port
if ":5432" in db_url:
    os.environ["DB_URL"] = db_url.replace(":5432", ":5440").replace("@timescaledb", "@localhost")

from orion.config import system_settings
from orion.connectors.uw_flow_connector import UWFlowConnector
from orion.processing.ingest_pipeline import ingest_bronze_events
from orion.processing.persistence import persist_silver_from_bronze
from orion.shared.utils import parse_timestamptz
from orion.storage.db import async_session_factory, init_db
from orion.storage.models import BronzeEvent

# Setup Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uw_backfill_fix")

RUN_ID = f"backfill_uw_fix_{datetime.now().strftime('%Y%m%d%H%M')}"


class RobustUWConnector(UWFlowConnector):
    """
    Subclass to override fetch logic with pagination and strict rate limit handling.
    """

    def fetch_all_pages(self, target_date: date) -> list:
        all_events = []
        seen_ids = set()
        offset = 0
        limit = 1000

        while True:
            logger.info(f"Fetching UW offset={offset} limit={limit}...")
            batch = self._fetch_page(target_date, offset, limit)

            if not batch:
                logger.info("No more events found.")
                break

            # Infinite Loop Guard: Check if we just fetched exact duplicates
            new_in_batch = 0
            for item in batch:
                # Assuming 'id' is standard
                eid = str(item.get("id"))
                if eid not in seen_ids:
                    seen_ids.add(eid)
                    new_in_batch += 1

            if new_in_batch == 0 and len(batch) > 0:
                logger.warning(
                    "Fetched batch contains only duplicate IDs. API might ignore offset. Stopping to prevent infinite loop."
                )
                break

            all_events.extend(batch)
            logger.info(f"Got {len(batch)} events ({new_in_batch} new). Total unique: {len(seen_ids)}")

            if len(batch) < limit:
                logger.info("Batch smaller than limit, incomplete page. Finishing.")
                break

            offset += limit
            # Be nice to the API
            time.sleep(1.0)

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
            response = self.session.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            # Extract list
            if isinstance(data, dict) and "data" in data:
                return data["data"]
            elif isinstance(data, list):
                return data
            else:
                logger.warning(f"Unexpected response format: {type(data)}")
                return []

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                logger.warning("Rate Limit Hit (429)! Retrying after backoff...")
                raise e  # Trigger tenacity
            raise e


async def main():
    target_date = date(2025, 12, 23)
    logger.info(f"Starting Robust UW Backfill for {target_date}")

    await init_db()

    connector = RobustUWConnector(api_key=system_settings.uw_api_key)

    # 1. Fetch
    raw_events = await asyncio.to_thread(connector.fetch_all_pages, target_date)
    logger.info(f"Total Raw Events Fetched: {len(raw_events)}")

    # 2. Convert to Bronze
    bronze_events = []
    seen = set()

    for raw in raw_events:
        try:
            # Standard normalization logic
            event_id = connector._generate_event_id(raw)
            if event_id in seen:
                continue
            seen.add(event_id)

            ts_str = raw.get("timestamp") or raw.get("created_at")
            event_ts = parse_timestamptz(ts_str, strict=True)

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
        except Exception:
            continue

    logger.info(f"Converted {len(bronze_events)} Bronze Events")

    # 3. Ingest
    if bronze_events:
        async with async_session_factory() as session:
            logger.info("Ingesting events (Dedup + Persist)...")
            unique_events = await ingest_bronze_events(session, bronze_events, run_id=RUN_ID, trace_id="manual_fix")
            logger.info(f"Unique new events: {len(unique_events)}")

            from orion.processing.persistence import persist_bronze_events

            await persist_bronze_events(session, unique_events)
            await persist_silver_from_bronze(session, unique_events)

            await session.commit()
            logger.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
