import asyncio
import hashlib
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from orion.core.errors import ErrorCode, ProviderError
from orion.shared.db_utils import db_query, db_write
from orion.shared.utils import parse_timestamptz
from orion.storage.models import BronzeEvent

logger = logging.getLogger(__name__)


class UWFlowConnector:
    """
    Connects to the Data Gateway to poll for Unusual Whales options flow events.
    Implements watermark polling and deduplication.
    """

    def __init__(self, gateway_url: Optional[str] = None, gateway_key: Optional[str] = None):
        self.gateway_url = gateway_url or os.getenv("GATEWAY_URL", "http://localhost:8080")
        self.gateway_key = gateway_key or os.getenv("GATEWAY_API_KEY", "gw_orion_trading_key_55555")

        self.session = requests.Session()
        self.session.headers.update({"X-Gateway-Key": self.gateway_key, "User-Agent": "Orion/0.1.0"})

        # State tracking
        self.last_poll_ts: Optional[datetime] = None
        self._watermark_loaded: bool = False
        self._watermark_key: str = "uw_flow"

    def _generate_event_id(self, event_data: Dict[str, Any]) -> str:
        """
        Generates a deterministic event ID based on the event content.
        PRD Rule 6.1: if provider gives unique ID use it, else hash(source + type + ticker + ts + payload).
        UW usually provides an 'id', but we'll encompass that logic here.
        """
        # Assuming UW provides an 'id' field in their flow object
        if "id" in event_data:
            unique_str = f"UW_FLOW_{event_data['id']}"
        else:
            # Fallback for robustness
            stable_payload = f"{event_data.get('ticker')}_{event_data.get('timestamp')}_{event_data.get('premium')}"
            unique_str = f"UW_FLOW_HASH_{stable_payload}"

        return hashlib.sha256(unique_str.encode()).hexdigest()

    @staticmethod
    def _is_retryable_fetch_error(exc: BaseException) -> bool:
        if isinstance(exc, requests.RequestException):
            return True
        if isinstance(exc, ProviderError) and exc.code in {
            ErrorCode.PROVIDER_RATE_LIMIT,
            ErrorCode.PROVIDER_TIMEOUT,
        }:
            return True
        return False

    async def _check_circuit_breaker(self) -> bool:
        """Check if circuit breaker is open. Returns True if should skip fetch."""
        from orion.core.circuit_breaker import CircuitBreaker

        if await CircuitBreaker().is_open():
            logger.warning(
                "Circuit breaker OPEN, skipping UW flow poll",
                extra={"event_type": "CIRCUIT_BREAKER_SKIP", "component": "UW_FLOW"},
            )
            return True
        return False

    @staticmethod
    def _normalize_put_call(raw: Dict[str, Any]) -> None:
        """Normalize put_call field to C/P format in-place."""
        if "put_call" not in raw and "type" in raw:
            t = raw["type"].upper()
            if t == "CALL":
                raw["put_call"] = "C"
            elif t == "PUT":
                raw["put_call"] = "P"
            else:
                raw["put_call"] = t[:1] if t else None

    @staticmethod
    def _normalize_premium(raw: Dict[str, Any]) -> None:
        """Normalize premium field from total_premium if needed."""
        if "premium" not in raw and "total_premium" in raw:
            raw["premium"] = raw["total_premium"]

    def _parse_single_event(
        self, raw: Dict[str, Any], fetch_start: datetime, now: datetime, seen_ids: set[str]
    ) -> BronzeEvent | None:
        """Parse a single raw event into a BronzeEvent, or return None if invalid."""
        ts_str = raw.get("timestamp") or raw.get("created_at")
        event_ts = parse_timestamptz(ts_str, strict=True)

        if event_ts < fetch_start:
            return None

        event_id = self._generate_event_id(raw)
        if event_id in seen_ids:
            return None
        seen_ids.add(event_id)

        source_event_id = str(raw.get("id")) if raw.get("id") is not None else None

        self._normalize_premium(raw)
        self._normalize_put_call(raw)

        return BronzeEvent(
            event_id=event_id,
            source="UW",
            source_event_id=source_event_id,
            event_type="UW_FLOW",
            event_ts_utc=event_ts,
            received_ts_utc=now,
            payload=raw,
            session="REG",
        )

    async def _update_watermark(self, events: List[BronzeEvent], now: datetime) -> None:
        """Update watermark based on processed events."""
        if events:
            candidate = max(e.event_ts_utc for e in events)
            if self.last_poll_ts is None or candidate > self.last_poll_ts:
                self.last_poll_ts = candidate
                await self._persist_watermark(self.last_poll_ts)
        elif self.last_poll_ts is None:
            self.last_poll_ts = now
            await self._persist_watermark(self.last_poll_ts)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_is_retryable_fetch_error),
    )
    def fetch_flow_for_date(self, trading_date: date) -> List[Dict[str, Any]]:
        """
        Fetches flow events for a given trading date (UTC).
        """
        # Enforce Rate Limit (120/min = 1/0.5s). Sleep 0.6s to be safe.
        time.sleep(0.6)

        try:
            # Use Data Gateway flow endpoint
            url = f"{self.gateway_url}/api/v1/uw/flow/all"
            from orion.config import system_settings

            params = {"date": trading_date.strftime("%Y-%m-%d"), "limit": system_settings.uw_fetch_limit}

            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()

            # Log API usage headers for quota monitoring
            daily_count = response.headers.get("x-uw-daily-req-count")
            daily_limit = response.headers.get("x-uw-token-req-limit")
            if daily_count and daily_limit:
                usage_pct = round(100 * int(daily_count) / int(daily_limit), 1)
                logger.info(
                    f"UW API usage: {daily_count}/{daily_limit} ({usage_pct}%)",
                    extra={
                        "event_type": "UW_API_USAGE",
                        "daily_count": int(daily_count),
                        "daily_limit": int(daily_limit),
                        "usage_pct": usage_pct,
                    },
                )

            data = response.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                code = ErrorCode.PROVIDER_AUTH_FAILED
            elif e.response.status_code == 429:
                code = ErrorCode.PROVIDER_RATE_LIMIT
            else:
                code = ErrorCode.PROVIDER_TIMEOUT  # approximation for generic HTTP error

            logger.error(
                "UW API Request Failed",
                extra={
                    "event_type": "UW_FLOW_FETCH_ERROR",
                    "error_code": code.value,
                    "status_code": e.response.status_code,
                },
            )
            raise ProviderError(f"HTTP Error: {e}", code=code) from e
        except Exception as e:
            logger.error(
                "UW API Connection Failed",
                extra={
                    "event_type": "UW_FLOW_FETCH_ERROR",
                    "error_code": ErrorCode.PROVIDER_TIMEOUT.value,
                    "error_details": str(e),
                },
            )
            raise ProviderError(f"Connection Error: {e}", code=ErrorCode.PROVIDER_TIMEOUT) from e

        # Strict Check: If dict, MUST have 'data' key which is a list.
        events = None
        if isinstance(data, list):
            events = data
        elif isinstance(data, dict):
            if "data" in data:
                events = data["data"]
            else:
                events = None

        if not isinstance(events, list):
            logger.error(
                "UW Parse Error",
                extra={
                    "event_type": "UW_FLOW_PARSE_ERROR",
                    "error_code": ErrorCode.PROVIDER_SCHEMA_DRIFT.value,
                    "payload_snippet": str(data)[:200],
                },
            )
            raise ProviderError(
                f"Invalid UW Flow API response format: {type(data)}", code=ErrorCode.PROVIDER_SCHEMA_DRIFT
            )

        from orion.config import system_settings

        if len(events) >= system_settings.uw_fetch_limit:
            logger.warning(
                "UW flow fetch hit configured limit; results may be truncated",
                extra={
                    "event_type": "UW_FLOW_FETCH_TRUNCATED",
                    "date": trading_date.strftime("%Y-%m-%d"),
                    "limit": system_settings.uw_fetch_limit,
                    "count": len(events),
                },
            )

        return events

    async def _ensure_watermark_loaded(self) -> None:
        if self._watermark_loaded:
            return
        from orion.storage.watermarks import get_watermark

        async def fetch_watermark(session: Any) -> None:
            return await get_watermark(session, key=self._watermark_key)

        wm = await db_query(fetch_watermark)
        if wm is not None:
            self.last_poll_ts = wm
        self._watermark_loaded = True

    async def _persist_watermark(self, ts: datetime) -> None:
        from orion.storage.watermarks import upsert_watermark

        async def update_watermark(session: Any) -> None:
            await upsert_watermark(session, key=self._watermark_key, last_seen_ts_utc=ts)

        await db_write(update_watermark)

    def _fetch_raw_events_sync(self, start_ts: datetime, end_ts: datetime) -> List[Dict[str, Any]]:
        """
        Fetch raw events for a [start_ts, end_ts] window.
        The UW flow endpoint used here is date-based; we fetch by UTC dates and filter client-side.
        """
        if start_ts.tzinfo is None:
            start_ts = start_ts.replace(tzinfo=timezone.utc)
        if end_ts.tzinfo is None:
            end_ts = end_ts.replace(tzinfo=timezone.utc)

        start_date = start_ts.astimezone(timezone.utc).date()
        end_date = end_ts.astimezone(timezone.utc).date()

        all_raw: List[Dict[str, Any]] = []
        cursor = start_date
        while cursor <= end_date:
            all_raw.extend(self.fetch_flow_for_date(cursor))
            cursor = cursor + timedelta(days=1)

        return all_raw

    async def fetch_raw_events(self, start_ts: datetime, end_ts: datetime) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._fetch_raw_events_sync, start_ts, end_ts)

    async def send_heartbeat_async(self) -> None:
        """
        Upserts heartbeat to SystemStatus table.
        PRD 8.1 / 15.4: Ingestion heartbeat to DB.
        """
        from sqlalchemy import select

        from orion.storage.db import async_session_factory
        from orion.storage.models import SystemStatus

        try:
            # Also log standard event for log ingestion tools
            logger.info(
                "HEARTBEAT",
                extra={
                    "event_type": "HEARTBEAT",
                    "component": "UW_FLOW",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                },
            )

            async with async_session_factory() as session:
                stmt = select(SystemStatus).where(SystemStatus.key == "global_health")
                result = await session.execute(stmt)
                record = result.scalars().first()

                if not record:
                    record = SystemStatus(key="global_health", status="HEALTHY")
                    session.add(record)

                record.status = "HEALTHY"
                record.last_updated_utc = datetime.now(timezone.utc)
                record.details = "UW Flow Connector Active"

                await session.commit()

        except Exception as e:
            logger.error(
                f"Failed to update DB Heartbeat: {e}", extra={"event_type": "HEARTBEAT_DB_ERROR", "error": str(e)}
            )

    async def poll(self, lookback_seconds: int = 120, overlap_seconds: int = 120) -> List[BronzeEvent]:
        """
        Polls for new events (Async).
        PRD 7.1: request events after (last_seen_ts - overlap_margin).
        """
        if await self._check_circuit_breaker():
            return []

        await self._ensure_watermark_loaded()
        await self.send_heartbeat_async()

        now = datetime.now(timezone.utc)
        poll_start_ts = self.last_poll_ts or (now - timedelta(seconds=lookback_seconds))
        fetch_start = poll_start_ts - timedelta(seconds=overlap_seconds) if self.last_poll_ts else poll_start_ts

        try:
            t0 = time.perf_counter()
            raw_events = await self.fetch_raw_events(fetch_start, now)
            duration_ms = (time.perf_counter() - t0) * 1000

            logger.info(
                f"Fetched {len(raw_events)} events in {duration_ms:.2f}ms",
                extra={"event_type": "FETCH_SUCCESS", "duration_ms": duration_ms, "count": len(raw_events)},
            )

        except ProviderError as e:
            logger.warning(
                f"Provider error during UW flow fetch: {e}",
                extra={"event_type": "UW_FLOW_PROVIDER_ERROR", "error": str(e)},
            )
            raise

        bronze_events = []
        seen_event_ids: set[str] = set()

        for raw in raw_events:
            try:
                event = self._parse_single_event(raw, fetch_start, now, seen_event_ids)
                if event:
                    bronze_events.append(event)
            except Exception as e:
                from orion.shared.dlq_utils import DLQWriter

                await DLQWriter.write_to_dlq(
                    error=e,
                    event_type="UW_FLOW_PARSE_ERROR",
                    source="UWFlowConnector",
                    payload=raw,
                    context="Failed to parse raw event in poll loop",
                    source_event_id=str(raw.get("id")) if raw.get("id") is not None else None,
                    ticker=raw.get("ticker"),
                    event_ts_utc=(
                        parse_timestamptz(raw.get("timestamp"), strict=False) if raw.get("timestamp") else None
                    ),
                )

        await self._update_watermark(bronze_events, now)
        return bronze_events

    async def fetch_since(self, ts: datetime, *, overlap_seconds: int = 120) -> List[BronzeEvent]:
        """
        PRDv2 7.2: Polling interface shim (fetch_since) so downstream code can stay stable
        when swapping transport (polling vs websocket).
        """
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        self.last_poll_ts = ts
        return await self.poll(lookback_seconds=0, overlap_seconds=overlap_seconds)
