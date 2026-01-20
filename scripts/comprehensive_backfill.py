import argparse
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
from orion.connectors.uw_flow_connector import UWFlowConnector
from orion.processing.ingest_pipeline import ingest_bronze_events
from orion.processing.persistence import persist_bronze_events, persist_silver_from_bronze
from orion.shared.utils import parse_timestamptz
from orion.storage.models import BronzeEvent

RUN_ID = f"comprehensive_backfill_{datetime.now().strftime('%Y%m%d%H%M')}"

# --- Robust Connector Subclasses ---


class RobustUWConnector(UWFlowConnector):
    """Overrides fetch logic to handle cursor pagination for Flow/Alerts.

    UW's `/option-trades/flow-alerts` endpoint is cursor-based (`older_than` / `newer_than`), not offset-based,
    and its effective max page size appears capped (e.g. 500).
    """

    def fetch_day(self, target_date: date) -> list[dict]:
        """
        Fetch *all* flow alerts for a specific UTC date by paging backwards using `older_than`.
        """
        all_events: list[dict] = []
        seen_ids: set[str] = set()

        limit = 500  # empirically, larger values can be ignored/capped unpredictably
        # Start just after end-of-day to ensure we capture the full day's range when paging backwards.
        cursor = datetime.combine(target_date + timedelta(days=1), dt_time(0, 0, tzinfo=timezone.utc)).isoformat()

        while True:
            logger.info(f"[UW Flow] Fetching older_than={cursor} limit={limit}...")
            batch = self._fetch_page(older_than=cursor, limit=limit)
            if not batch:
                logger.info("[UW Flow] No more events found.")
                break

            # Determine the next cursor from the oldest timestamp in the batch.
            # We cannot rely on response-provided cursors (they can reflect server 'now').
            def _ts(item: dict) -> str:
                return str(item.get("timestamp") or item.get("created_at") or "")

            oldest_ts = min((_ts(it) for it in batch if _ts(it)), default=None)
            if not oldest_ts:
                logger.warning("[UW Flow] Batch missing timestamps; stopping.")
                break

            # Keep only events that match the requested day; stop once we've paged into earlier dates.
            batch_dates = {(_ts(it)[:10]) for it in batch if _ts(it)}
            if batch_dates and min(batch_dates) < target_date.isoformat():
                # We've crossed into the prior day; still ingest the in-day items and stop.
                pass

            new_in_batch = 0
            for item in batch:
                ts = _ts(item)
                if not ts or ts[:10] != target_date.isoformat():
                    continue
                eid = str(item.get("id") or "")
                if not eid or eid in seen_ids:
                    continue
                seen_ids.add(eid)
                all_events.append(item)
                new_in_batch += 1

            logger.info(f"[UW Flow] Batch {len(batch)} items, +{new_in_batch} in-day new, total={len(all_events)}")

            # Stop condition: if the oldest item is already before target day, we've exhausted the day.
            if oldest_ts[:10] < target_date.isoformat():
                break

            # Advance cursor backward. If the server treats `older_than` as inclusive, we can get stuck
            # returning the same oldest record repeatedly. Detect and stop to avoid infinite loops.
            if oldest_ts == cursor:
                logger.warning("[UW Flow] Pagination cursor did not advance (inclusive older_than). Stopping.")
                break
            cursor = oldest_ts

            # Rate limit niceness
            time.sleep(0.6)

        return all_events

    @retry(
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        stop=stop_after_attempt(10),
        wait=wait_exponential(multiplier=2, min=5, max=60),
    )
    def _fetch_page(self, *, older_than: str, limit: int) -> list:
        url = f"{self.base_url}/option-trades/flow-alerts"
        params = {"limit": limit, "older_than": older_than}
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
                logger.warning(f"[UW Flow] 429 Rate Limit! Usage: {d_c}/{l_t}. Backing off...")
                # The @retry decorator handles the wait
                raise e
            raise e


# --- Raw HTTP helpers (avoid SDK model parsing issues) ---


def _uw_headers() -> dict[str, str]:
    token = system_settings.uw_api_key
    if not token:
        raise RuntimeError("Missing UW_API_KEY")
    return {"Authorization": f"Bearer {token}"}


def _uw_base() -> str:
    return os.getenv("UW_BASE_URL", "https://api.unusualwhales.com/api").rstrip("/")


@retry(
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    stop=stop_after_attempt(10),
    wait=wait_exponential(multiplier=2, min=2, max=30),
)
def _uw_get_json(path: str, *, params: dict[str, object]) -> dict:
    url = f"{_uw_base()}{path}"
    resp = requests.get(url, params=params, headers=_uw_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        return data
    return {"data": data}


@retry(
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
)
def _uw_get_json_fast(path: str, *, params: dict[str, object]) -> dict:
    """
    Faster/shallower retry policy for high-fanout endpoints (e.g., per-ticker darkpool).
    """
    url = f"{_uw_base()}{path}"
    resp = requests.get(url, params=params, headers=_uw_headers(), timeout=15)
    # Some endpoints return 4xx when there's simply no data for that symbol/date.
    # Treat these as empty rather than retrying.
    if resp.status_code in (404, 422):
        return {"data": []}
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        return data
    return {"data": data}


def fetch_uw_alerts_day(date_target: date) -> list[dict]:
    """
    Fetch all UW alerts for a specific UTC date by paging backwards using `older_than`.
    The endpoint is cursor-based and may not support an explicit `limit`, so we rely on cursor progress.
    """
    all_items: list[dict] = []
    seen_ids: set[str] = set()

    cursor = datetime.combine(date_target + timedelta(days=1), dt_time(0, 0, tzinfo=timezone.utc)).isoformat()

    while True:
        payload = _uw_get_json("/alerts", params={"older_than": cursor})
        items = payload.get("data") or []
        if not isinstance(items, list) or not items:
            break

        def _ts(it: dict) -> str:
            return str(it.get("timestamp") or it.get("created_at") or "")

        oldest_ts = min((_ts(it) for it in items if _ts(it)), default=None)
        if not oldest_ts:
            break

        new_in_batch = 0
        for it in items:
            ts = _ts(it)
            if not ts or ts[:10] != date_target.isoformat():
                continue
            sid = str(it.get("id") or "")
            if sid and sid in seen_ids:
                continue
            if sid:
                seen_ids.add(sid)
            all_items.append(it)
            new_in_batch += 1

        logger.info(f"[UW Alerts] Batch {len(items)} items, +{new_in_batch} in-day new, total={len(all_items)}")

        if oldest_ts[:10] < date_target.isoformat():
            break
        if oldest_ts == cursor:
            logger.warning("[UW Alerts] Pagination cursor did not advance; stopping.")
            break

        cursor = oldest_ts
        time.sleep(0.6)

    return all_items


def fetch_uw_darkpool_day(date_target: date, tickers: list[str]) -> list[dict]:
    """
    Fetch UW darkpool trades for the given tickers for a date.
    Uses per-ticker endpoint to avoid model parsing issues seen in the SDK.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    all_items: list[dict] = []

    def _fetch_one(ticker: str) -> list[dict]:
        # UW_BASE_URL here includes `/api`, so per-ticker path is `/darkpool/{ticker}` (no extra `/api`).
        payload = _uw_get_json_fast(f"/darkpool/{ticker}", params={"date": date_target.isoformat()})
        items = payload.get("data") or []
        if not isinstance(items, list):
            return []
        out: list[dict] = []
        for it in items:
            if isinstance(it, dict):
                it.setdefault("ticker", ticker)
                out.append(it)
        return out

    # Keep workers modest to stay under UW rate limits.
    max_workers = min(5, max(1, len(tickers)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_one, t): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                all_items.extend(fut.result())
            except Exception as e:
                logger.warning(f"[UW DarkPool] Failed ticker={t} date={date_target}: {e}")

    return all_items


# --- Helpers ---


async def get_db_url_and_engine():
    """Try to connect to DB with various credentials, return working URL and engine."""

    # Prioritize fallback credentials if default is known to fail
    # Note: If DB_URL already has correct credentials from previous fix, it should work.
    urls = [
        os.getenv("DB_URL"),
        "postgresql+asyncpg://postgres:password@localhost:5440/orion_db",  # pragma: allowlist secret
        "postgresql+asyncpg://postgres:postgres@localhost:5440/orion_db",  # pragma: allowlist secret
        "postgresql+asyncpg://orion:orion_password@localhost:5440/orion_db",  # pragma: allowlist secret
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
        raw_rows = await asyncio.to_thread(uw_conn.fetch_day, date_target)

        for raw in raw_rows:
            try:
                # Normalization
                if "premium" not in raw and "total_premium" in raw:
                    raw["premium"] = raw["total_premium"]
                if "put_call" not in raw and "type" in raw:
                    t = raw["type"].upper()
                    raw["put_call"] = "C" if t == "CALL" else ("P" if t == "PUT" else t[:1])

                ticker = raw.get("ticker") or raw.get("underlying") or raw.get("underlying_symbol") or raw.get("symbol")
                if not ticker:
                    # Silver schema requires ticker; skip malformed records rather than failing the whole batch.
                    continue
                # Ensure payload ticker is present (do not use setdefault; API can include ticker=None).
                raw["ticker"] = ticker

                eid = uw_conn._generate_event_id(raw)
                ts_str = raw.get("timestamp") or raw.get("created_at")

                events_to_ingest.append(
                    BronzeEvent(
                        event_id=eid,
                        source="UW",
                        source_event_id=str(raw.get("id")) if raw.get("id") else None,
                        event_type="UW_FLOW",
                        ticker=ticker,
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

    # 2. UW Alerts
    try:
        current_len = len(events_to_ingest)
        raw_alerts = await asyncio.to_thread(fetch_uw_alerts_day, date_target)
        for raw in raw_alerts:
            try:
                # Alerts typically have an id
                sid = raw.get("id")
                eid = (
                    hashlib.sha256(f"UW_ALERT_{sid}".encode("utf-8")).hexdigest()
                    if sid
                    else hashlib.sha256(str(raw).encode("utf-8")).hexdigest()
                )
                ts_str = raw.get("timestamp") or raw.get("created_at")
                ticker = raw.get("ticker") or raw.get("symbol") or raw.get("underlying") or raw.get("underlying_symbol")
                events_to_ingest.append(
                    BronzeEvent(
                        event_id=eid,
                        source="UW",
                        source_event_id=str(sid) if sid is not None else None,
                        event_type="UW_ALERT",
                        ticker=ticker,
                        event_ts_utc=parse_timestamptz(ts_str, strict=True),
                        received_ts_utc=datetime.now(timezone.utc),
                        payload=raw,
                        session="REG",
                    )
                )
            except Exception:
                pass

        logger.info(
            f"[UW Alerts] Collected {len(raw_alerts)} raw events -> {len(events_to_ingest) - current_len} added"
        )
    except Exception as e:
        logger.error(f"[UW Alerts] Failed: {e}")

    # 3. UW Dark Pool (per ticker)
    try:
        current_len = len(events_to_ingest)
        raw_dp = await asyncio.to_thread(fetch_uw_darkpool_day, date_target, active_tickers)

        for raw in raw_dp:
            try:
                ticker = raw.get("ticker")
                ts_str = raw.get("executed_at") or raw.get("timestamp") or raw.get("date")
                # Deterministic hash with (ticker, ts, price, size) if no id.
                sid = raw.get("id") or raw.get("id_")
                if sid:
                    eid = hashlib.sha256(f"UW_DARKPOOL_{sid}".encode("utf-8")).hexdigest()
                else:
                    eid = hashlib.sha256(
                        f"UW_DARKPOOL_{ticker}_{raw.get('price')}_{raw.get('size')}_{ts_str}".encode("utf-8")
                    ).hexdigest()

                events_to_ingest.append(
                    BronzeEvent(
                        event_id=eid,
                        source="UW",
                        source_event_id=str(sid) if sid is not None else None,
                        event_type="UW_DARKPOOL",
                        ticker=ticker,
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

    # 4. Alpaca Bars
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

        # Guardrails: Silver schemas require non-null ticker for UW flow/darkpool/alerts and Alpaca bars.
        # Drop malformed rows rather than failing the whole day.
        unique = [
            e
            for e in unique
            if (e.event_type not in ("UW_FLOW", "UW_DARKPOOL", "UW_ALERT", "ALPACA_BAR_1M"))
            or getattr(e, "ticker", None)
        ]

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
