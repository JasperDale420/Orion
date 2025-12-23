import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from tenacity import retry, stop_after_attempt, wait_exponential

from orion.shared.utils import parse_timestamptz
from orion.storage.models import BronzeEvent
from orion.unusualwhales.api.alerts import get_alerts
from orion.unusualwhales.client import UnusualWhalesClient

logger = logging.getLogger(__name__)


class UWAlertsConnector:
    """
    Connects to Unusual Whales API to poll for Flow Alerts.
    """

    def __init__(self, api_key: str, base_url: str):
        self.client = UnusualWhalesClient(base_url=base_url, token=api_key)
        self.last_seen_id: Optional[str] = None
        self.last_poll_ts: Optional[datetime] = None
        self._watermark_loaded: bool = False
        self._watermark_key: str = "uw_alerts"

    def _generate_event_id(self, event_data: dict) -> str:
        """
        Generates a deterministic event ID.
        """
        # Alerts typically have an 'id'.
        source_id = event_data.get("id")
        if source_id:
            return hashlib.sha256(f"UW_ALERT_{source_id}".encode("utf-8")).hexdigest()

        # Fallback
        raw_str = f"UW_ALERT_{event_data.get('ticker')}_{event_data.get('timestamp')}_{event_data.get('strike')}"
        return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def fetch_events(self, lookback_seconds: int = 120, overlap_seconds: int = 120) -> List[BronzeEvent]:
        """
        Fetches latest flow alerts.
        """
        # Enforce Rate Limit
        time.sleep(0.6)
        try:
            await self._ensure_watermark_loaded()

            now = datetime.now(timezone.utc)
            poll_start_ts = self.last_poll_ts or (now - timedelta(seconds=lookback_seconds))
            fetch_start = (
                poll_start_ts if self.last_poll_ts is None else (poll_start_ts - timedelta(seconds=overlap_seconds))
            )

            response = await self._fetch_raw_events(newer_than=fetch_start)

            if not response:
                return []

            data = response
            if isinstance(response, dict) and "data" in response:
                data = response["data"]

            if not isinstance(data, list):
                logger.warning(f"Unexpected response format from UW Alerts: {type(data)}")
                return []

            events: List[BronzeEvent] = []
            seen_event_ids: set[str] = set()

            for item in data:
                try:
                    event_id = self._generate_event_id(item)
                    if event_id in seen_event_ids:
                        continue
                    seen_event_ids.add(event_id)
                    source_event_id = item.get("id")

                    # Try to extract timestamp
                    ts_str = item.get("timestamp") or item.get("created_at")
                    if not ts_str:
                        raise ValueError("Missing timestamp/created_at in UW alert payload")
                    events_ts = parse_timestamptz(ts_str, strict=True)

                    if events_ts < fetch_start:
                        continue

                    # Normalize put_call to C/P
                    if "put_call" in item:
                        pc = item["put_call"].upper()
                        item["put_call"] = "C" if pc == "CALL" else ("P" if pc == "PUT" else pc[:1])
                    elif "type" in item:
                        pc = item["type"].upper()
                        item["put_call"] = "C" if pc == "CALL" else ("P" if pc == "PUT" else pc[:1])

                    events.append(
                        BronzeEvent(
                            event_id=event_id,
                            source="UW",
                            source_event_id=str(source_event_id) if source_event_id is not None else None,
                            event_type="UW_ALERT",
                            event_ts_utc=events_ts,
                            payload=item,
                            session="REG",
                        )
                    )
                except Exception as e:
                    # Granular DLQ Handling per Event
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
                    continue

            if events:
                candidate = max(e.event_ts_utc for e in events if e.event_ts_utc)
                if self.last_poll_ts is None or candidate > self.last_poll_ts:
                    self.last_poll_ts = candidate
                    await self._persist_watermark(self.last_poll_ts)
            elif self.last_poll_ts is None:
                self.last_poll_ts = now
                await self._persist_watermark(self.last_poll_ts)

            return events

        except Exception as e:
            logger.error(f"Error fetching UW Alerts: {e}")
            raise e

    async def fetch_since(self, ts: datetime, *, overlap_seconds: int = 120) -> List[BronzeEvent]:
        """
        PRDv2 7.2: Polling interface shim (fetch_since).
        UW alerts endpoint supports newer_than; we still keep an overlap window.
        """
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        self.last_poll_ts = ts
        return await self.fetch_events(lookback_seconds=0, overlap_seconds=overlap_seconds)

    async def _ensure_watermark_loaded(self) -> None:
        if self._watermark_loaded:
            return
        from orion.storage.db import async_session_factory
        from orion.storage.watermarks import get_watermark

        async with async_session_factory() as session:
            wm = await get_watermark(session, key=self._watermark_key)
            if wm is not None:
                self.last_poll_ts = wm
        self._watermark_loaded = True

    async def _persist_watermark(self, ts: datetime) -> None:
        from orion.storage.db import async_session_factory
        from orion.storage.watermarks import upsert_watermark

        async with async_session_factory() as session:
            await upsert_watermark(session, key=self._watermark_key, last_seen_ts_utc=ts)

    async def _fetch_raw_events(self, *, newer_than: datetime) -> object:
        import asyncio

        return await asyncio.to_thread(
            get_alerts.sync,
            client=self.client,
            newer_than=newer_than.isoformat(),
        )
