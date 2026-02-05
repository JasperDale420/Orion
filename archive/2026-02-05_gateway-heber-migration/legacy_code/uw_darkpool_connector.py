import asyncio
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from orion.shared.db_utils import db_query, db_write
from orion.shared.utils import parse_timestamptz
from orion.storage.models import BronzeEvent
from orion.storage.watermarks import get_watermark, upsert_watermark
from orion.unusualwhales.api.darkpool import get_trades_by_date
from orion.unusualwhales.client import UnusualWhalesClient
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class UWDarkPoolConnector:
    """
    Connects to Data Gateway to poll for Unusual Whales Dark Pool prints.
    """

    def __init__(self, gateway_url: Optional[str] = None, gateway_key: Optional[str] = None):
        gateway_url = gateway_url or os.getenv("GATEWAY_URL", "http://localhost:8080")
        gateway_key = gateway_key or os.getenv("GATEWAY_API_KEY", "gw_orion_trading_key_55555")
        # Use Gateway URL for UW endpoints (auth handled by Gateway)
        self.client = UnusualWhalesClient(base_url=f"{gateway_url}/api/v1/uw", token=gateway_key)
        self.last_seen_id: Optional[str] = None
        self.last_poll_ts: Optional[datetime] = None
        self._watermark_loaded: bool = False
        self._watermark_key: str = "uw_darkpool"

    def _generate_event_id(self, event_data: dict[str, Any]) -> str:
        """
        Generates a deterministic event ID.
        Prefer source event ID if available, otherwise hash content.
        """
        # Dark pool prints usually have an 'id' field?
        # Let's check the endpoint response structure if possible, but assuming standard flow.
        # If 'id' or 'id_' exists, use it.
        source_id = event_data.get("id") or event_data.get("id_")
        if source_id:
            return hashlib.sha256(f"UW_DARKPOOL_{source_id}".encode("utf-8")).hexdigest()

        # Fallback: Hash content
        # Use stable subset: ticker, price, size, timestamp
        raw_str = f"UW_DARKPOOL_{event_data.get('ticker')}_{event_data.get('price')}_{event_data.get('size')}_{event_data.get('timestamp')}"
        return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

    async def _check_circuit_breaker(self) -> bool:
        """Check if circuit breaker is open. Returns True if should skip fetch."""
        from orion.core.circuit_breaker import CircuitBreaker

        if await CircuitBreaker().is_open():
            logger.warning(
                "Circuit breaker OPEN, skipping UW darkpool fetch",
                extra={"event_type": "CIRCUIT_BREAKER_SKIP", "component": "UW_DARKPOOL"},
            )
            return True
        return False

    @staticmethod
    def _resolve_ticker(item: dict[str, Any]) -> str | None:
        """Extract ticker from various possible payload fields."""
        return item.get("ticker") or item.get("symbol")

    def _parse_single_item(self, item: dict[str, Any], fetch_start: datetime, seen_ids: set[str]) -> BronzeEvent | None:
        """Parse a single raw item into a BronzeEvent, or return None if invalid."""
        event_id = self._generate_event_id(item)
        if event_id in seen_ids:
            return None
        seen_ids.add(event_id)

        ts_str = item.get("executed_at") or item.get("timestamp") or item.get("date")
        if not ts_str:
            raise ValueError("Missing executed_at/timestamp/date in UW darkpool payload")

        events_ts = parse_timestamptz(ts_str, strict=True)
        if events_ts < fetch_start:
            return None

        ticker = self._resolve_ticker(item)
        if not ticker:
            logger.warning(
                f"Skipping UW darkpool without ticker: id={item.get('id') or item.get('id_')}",
                extra={"event_type": "UW_DARKPOOL_MISSING_TICKER"},
            )
            return None

        source_event_id = item.get("id") or item.get("id_")
        return BronzeEvent(
            event_id=event_id,
            source="UW",
            source_event_id=str(source_event_id) if source_event_id is not None else None,
            event_type="UW_DARKPOOL",
            ticker=ticker,
            event_ts_utc=events_ts,
            payload=item,
            session="REG",
        )

    async def _update_watermark(self, events: List[BronzeEvent], now: datetime) -> None:
        """Update watermark based on processed events."""
        if events:
            candidate = max(e.event_ts_utc for e in events if e.event_ts_utc)
            if self.last_poll_ts is None or candidate > self.last_poll_ts:
                self.last_poll_ts = candidate
                await self._persist_watermark(self.last_poll_ts)
        elif self.last_poll_ts is None:
            self.last_poll_ts = now
            await self._persist_watermark(self.last_poll_ts)

    async def _fetch_all_raw_for_date_range(self, start_date: datetime, end_date: datetime) -> list[dict[str, Any]]:
        """Fetch raw events for a date range."""
        all_raw: list[dict[str, Any]] = []
        cursor = start_date.date()
        end = end_date.date()
        while cursor <= end:
            all_raw.extend(await self._fetch_raw_for_date(cursor.strftime("%Y-%m-%d")))
            cursor = cursor + timedelta(days=1)
        return all_raw

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def fetch_events(self, lookback_seconds: int = 120, overlap_seconds: int = 120) -> List[BronzeEvent]:
        """Fetches latest dark pool prints."""
        if await self._check_circuit_breaker():
            return []

        await asyncio.sleep(0.6)  # Rate limit

        try:
            await self._ensure_watermark_loaded()

            now = datetime.now(timezone.utc)
            poll_start_ts = self.last_poll_ts or (now - timedelta(seconds=lookback_seconds))
            fetch_start = poll_start_ts - timedelta(seconds=overlap_seconds) if self.last_poll_ts else poll_start_ts

            all_raw = await self._fetch_all_raw_for_date_range(fetch_start, now)

            events: List[BronzeEvent] = []
            seen_event_ids: set[str] = set()

            for item in all_raw:
                try:
                    event = self._parse_single_item(item, fetch_start, seen_event_ids)
                    if event:
                        events.append(event)
                except Exception as e:
                    from orion.shared.dlq_utils import DLQWriter

                    source_id = item.get("id") or item.get("id_")
                    await DLQWriter.write_to_dlq(
                        error=e,
                        event_type="UW_DARKPOOL_PARSE_ERROR",
                        source="UWDarkPoolConnector",
                        payload=item,
                        context="Failed to parse raw event in fetch loop",
                        source_event_id=str(source_id) if source_id is not None else None,
                        ticker=item.get("ticker"),
                        event_ts_utc=parse_timestamptz(
                            item.get("executed_at") or item.get("timestamp") or item.get("date"), strict=False
                        ),
                    )

            await self._update_watermark(events, now)
            return events

        except Exception as e:
            logger.error(f"Error fetching UW Dark Pool prints: {e}")
            raise

    async def fetch_since(self, ts: datetime, *, overlap_seconds: int = 120) -> List[BronzeEvent]:
        """
        PRDv2 7.2: Polling interface shim (fetch_since).
        UW darkpool endpoint is date-based; we filter client-side using timestamps.
        """
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        self.last_poll_ts = ts
        return await self.fetch_events(lookback_seconds=0, overlap_seconds=overlap_seconds)

    async def _ensure_watermark_loaded(self) -> None:
        if self._watermark_loaded:
            return

        async def fetch_watermark(session: Any) -> None:
            return await get_watermark(session, key=self._watermark_key)

        wm = await db_query(fetch_watermark)
        if wm is not None:
            self.last_poll_ts = wm
        self._watermark_loaded = True

    async def _persist_watermark(self, ts: datetime) -> None:
        async def update_watermark(session: Any) -> None:
            await upsert_watermark(session, key=self._watermark_key, last_seen_ts_utc=ts)

        await db_write(update_watermark)

    async def _fetch_raw_for_date(self, date_str: str) -> list[dict[str, Any]]:
        import asyncio

        try:
            response = await asyncio.to_thread(
                get_trades_by_date.sync,
                client=self.client,
                date=date_str,
            )
        except Exception as e:
            logger.warning(f"Failed to fetch UW Dark Pool for {date_str}: {e}")
            return []

        if not response:
            return []

        # Handle ErrorMessage responses (rate limiting, auth errors, etc.)
        if hasattr(response, "message") or (
            hasattr(response, "__class__") and "ErrorMessage" in response.__class__.__name__
        ):
            error_msg = getattr(response, "message", None) or str(response)
            logger.warning(
                f"UW Dark Pool API error for {date_str}: {error_msg}",
                extra={"event_type": "UW_DARKPOOL_API_ERROR", "error": error_msg},
            )
            return []

        # Handle Object Response (DarkpoolTradeResponse)
        if hasattr(response, "data") and isinstance(response.data, list):
            # Convert list of DarkpoolTrade objects to list of dicts
            return [item.to_dict() for item in response.data]

        data = response
        if isinstance(response, dict) and "data" in response:
            data = response["data"]

        if isinstance(data, list):
            # Ensure items are dicts (if list of dicts)
            return data

        logger.warning(f"Unexpected response format from UW Dark Pool: {type(data)}")
        return []
