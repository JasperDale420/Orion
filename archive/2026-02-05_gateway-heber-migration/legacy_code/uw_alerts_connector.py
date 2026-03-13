import asyncio
import hashlib
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from orion.shared.db_utils import db_query, db_write
from orion.shared.utils import parse_timestamptz
from orion.storage.models import BronzeEvent
from orion.storage.watermarks import get_watermark, upsert_watermark
from orion.unusualwhales.api.alerts import get_alerts
from orion.unusualwhales.client import UnusualWhalesClient
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class UWAlertsConnector:
    """
    Connects to Data Gateway to poll for Unusual Whales Flow Alerts.
    """

    def __init__(self, gateway_url: str | None = None, gateway_key: str | None = None):
        gateway_url = gateway_url or os.getenv("GATEWAY_URL", "http://localhost:8080")
        gateway_key = gateway_key or os.getenv("GATEWAY_API_KEY", "gw_orion_trading_key_55555")
        # Use Gateway URL for UW endpoints (auth handled by Gateway)
        self.client = UnusualWhalesClient(base_url=f"{gateway_url}/api/v1/uw", token=gateway_key)
        self.last_seen_id: str | None = None
        self.last_poll_ts: datetime | None = None
        self._watermark_loaded: bool = False
        self._watermark_key: str = "uw_alerts"

    def _generate_event_id(self, event_data: dict[str, Any]) -> str:
        """
        Generates a deterministic event ID.
        """
        source_id = event_data.get("id")
        if source_id:
            return hashlib.sha256(f"UW_ALERT_{source_id}".encode()).hexdigest()

        raw_str = f"UW_ALERT_{event_data.get('ticker')}_{event_data.get('timestamp')}_{event_data.get('strike')}"
        return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

    @staticmethod
    def _resolve_ticker(item: dict[str, Any]) -> str | None:
        """Extract ticker from various possible payload fields."""
        return (
            item.get("ticker")
            or item.get("symbol")
            or item.get("underlying")
            or item.get("underlying_symbol")
            or item.get("stock")
        )

    @staticmethod
    def _normalize_put_call(item: dict[str, Any]) -> None:
        """Normalize put_call field to C/P format in-place."""
        raw_value: str | None = None
        if "put_call" in item:
            raw_value = item["put_call"]
        elif "type" in item:
            raw_value = item["type"]

        if raw_value:
            upper = raw_value.upper()
            if upper == "CALL":
                item["put_call"] = "C"
            elif upper == "PUT":
                item["put_call"] = "P"
            else:
                item["put_call"] = upper[:1] if upper else None

    async def _check_circuit_breaker(self) -> bool:
        """Check if circuit breaker is open. Returns True if should skip fetch."""
        from orion.core.circuit_breaker import CircuitBreaker

        if await CircuitBreaker().is_open():
            logger.warning(
                "Circuit breaker OPEN, skipping UW alerts fetch",
                extra={"event_type": "CIRCUIT_BREAKER_SKIP", "component": "UW_ALERTS"},
            )
            return True
        return False

    def _extract_response_data(self, response: Any) -> list[dict[str, Any]] | None:
        """Extract list data from response, handling various formats."""
        if not response:
            return None

        data = response
        if isinstance(response, dict) and "data" in response:
            data = response["data"]

        if not isinstance(data, list):
            logger.warning(f"Unexpected response format from UW Alerts: {type(data)}")
            return None

        return data

    def _parse_single_item(self, item: dict[str, Any], fetch_start: datetime, seen_ids: set[str]) -> BronzeEvent | None:
        """Parse a single raw item into a BronzeEvent, or return None if invalid."""
        event_id = self._generate_event_id(item)
        if event_id in seen_ids:
            return None
        seen_ids.add(event_id)

        ts_str = item.get("timestamp") or item.get("created_at")
        if not ts_str:
            raise ValueError("Missing timestamp/created_at in UW alert payload")

        events_ts = parse_timestamptz(ts_str, strict=True)
        if events_ts < fetch_start:
            return None

        self._normalize_put_call(item)

        ticker = self._resolve_ticker(item)
        if not ticker:
            logger.warning(
                f"Skipping UW alert without ticker: id={item.get('id')}",
                extra={"event_type": "UW_ALERT_MISSING_TICKER"},
            )
            return None

        if not item.get("ticker"):
            item["ticker"] = ticker

        source_event_id = item.get("id")
        return BronzeEvent(
            event_id=event_id,
            source="UW",
            source_event_id=str(source_event_id) if source_event_id is not None else None,
            event_type="UW_ALERT",
            ticker=ticker,
            event_ts_utc=events_ts,
            payload=item,
            session="REG",
        )

    async def _update_watermark(self, events: list[BronzeEvent], now: datetime) -> None:
        """Update watermark based on processed events."""
        if events:
            candidate = max(e.event_ts_utc for e in events if e.event_ts_utc)
            if self.last_poll_ts is None or candidate > self.last_poll_ts:
                self.last_poll_ts = candidate
                await self._persist_watermark(self.last_poll_ts)
        elif self.last_poll_ts is None:
            self.last_poll_ts = now
            await self._persist_watermark(self.last_poll_ts)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def fetch_events(self, lookback_seconds: int = 120, overlap_seconds: int = 120) -> list[BronzeEvent]:
        """Fetches latest flow alerts."""
        if await self._check_circuit_breaker():
            return []

        await asyncio.sleep(0.6)  # Rate limit

        try:
            await self._ensure_watermark_loaded()

            now = datetime.now(UTC)
            poll_start_ts = self.last_poll_ts or (now - timedelta(seconds=lookback_seconds))
            fetch_start = poll_start_ts - timedelta(seconds=overlap_seconds) if self.last_poll_ts else poll_start_ts

            response = await self._fetch_raw_events(newer_than=fetch_start)
            data = self._extract_response_data(response)
            if data is None:
                return []

            events: list[BronzeEvent] = []
            seen_event_ids: set[str] = set()

            for item in data:
                try:
                    event = self._parse_single_item(item, fetch_start, seen_event_ids)
                    if event:
                        events.append(event)
                except Exception as e:
                    from orion.shared.dlq_utils import DLQWriter

                    await DLQWriter.write_to_dlq(
                        error=e,
                        event_type="UW_ALERT_PARSE_ERROR",
                        source="UWAlertsConnector",
                        payload=item,
                        context="Failed to parse raw event in fetch loop",
                        source_event_id=str(item.get("id")) if item.get("id") is not None else None,
                        ticker=item.get("ticker"),
                        event_ts_utc=parse_timestamptz(item.get("timestamp") or item.get("created_at"), strict=False),
                    )

            await self._update_watermark(events, now)
            return events

        except Exception as e:
            logger.error(f"Error fetching UW Alerts: {e}")
            raise e

    async def fetch_since(self, ts: datetime, *, overlap_seconds: int = 120) -> list[BronzeEvent]:
        """
        PRDv2 7.2: Polling interface shim (fetch_since).
        UW alerts endpoint supports newer_than; we still keep an overlap window.
        """
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
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

    async def _fetch_raw_events(self, *, newer_than: datetime) -> object:
        import asyncio

        return await asyncio.to_thread(
            get_alerts.sync,
            client=self.client,
            newer_than=newer_than.isoformat(),
        )
