"""
UW Market Tide Connector.

Fetches market-wide options flow sentiment (net call/put premium).
"""

import asyncio
import logging
from datetime import date, datetime
from typing import Any, Dict, Optional

import requests
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential

from orion.shared.db_utils import db_write

logger = logging.getLogger(__name__)


class UWMarketTideConnector:
    """Fetches market tide (net premium intraday) from UW API."""

    BASE_URL = "https://api.unusualwhales.com"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {"Authorization": f"Bearer {api_key}"}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _fetch_market_tide(self, market_date: Optional[date] = None) -> Optional[Dict[str, Any]]:
        """Fetch market tide for a date."""
        url = f"{self.BASE_URL}/api/market/market-tide"
        params = {}
        if market_date:
            params["date"] = market_date.isoformat()

        try:
            resp = requests.get(url, headers=self.headers, params=params, timeout=30)
            resp.raise_for_status()

            # Log API usage headers for quota monitoring
            daily_count = resp.headers.get("x-uw-daily-req-count")
            daily_limit = resp.headers.get("x-uw-token-req-limit")
            if daily_count and daily_limit:
                usage_pct = round(100 * int(daily_count) / int(daily_limit), 1)
                logger.info(
                    f"UW API usage: {daily_count}/{daily_limit} ({usage_pct}%)",
                    extra={"event_type": "UW_API_USAGE", "component": "market_tide"},
                )

            return resp.json()
        except Exception as e:
            logger.warning(f"Failed to fetch market tide: {e}")
            return None

    async def fetch_and_store(self, market_date: Optional[date] = None) -> int:
        """Fetch market tide and store all ticks."""
        data = await asyncio.to_thread(self._fetch_market_tide, market_date)
        if not data or "data" not in data:
            return 0

        ticks = data["data"]
        if not ticks:
            return 0

        stored = 0
        for tick in ticks:
            ts_str = tick.get("timestamp")
            if not ts_str:
                continue

            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except Exception:
                continue

            record = {
                "ts_utc": ts,
                "date": ts.date(),
                "net_call_premium": float(tick.get("net_call_premium") or 0),
                "net_put_premium": float(tick.get("net_put_premium") or 0),
                "net_volume": int(tick.get("net_volume") or 0),
            }

            await self._persist_tick(record)
            stored += 1

        return stored

    async def _persist_tick(self, record: Dict[str, Any]) -> None:
        """Persist market tide tick to database."""

        async def write(session: Any) -> None:
            stmt = text(
                """
                INSERT INTO silver_market_tide (
                    ts_utc, date, net_call_premium, net_put_premium, net_volume
                ) VALUES (
                    :ts_utc, :date, :net_call_premium, :net_put_premium, :net_volume
                )
                ON CONFLICT (ts_utc) DO NOTHING
            """
            )
            await session.execute(stmt, record)

        await db_write(write)
