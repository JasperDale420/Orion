"""
UW IV Rank Connector.

Fetches IV rank and percentile from Unusual Whales API.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential

from orion.shared.db_utils import db_write

logger = logging.getLogger(__name__)


class UWIVRankConnector:
    """Fetches IV rank/percentile from UW API."""

    BASE_URL = "https://api.unusualwhales.com"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {"Authorization": f"Bearer {api_key}"}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _fetch_iv_rank(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Fetch IV rank for a ticker."""
        url = f"{self.BASE_URL}/api/stock/{ticker}/iv-rank"
        try:
            resp = requests.get(url, headers=self.headers, timeout=30)
            resp.raise_for_status()

            # Log API usage headers for quota monitoring
            daily_count = resp.headers.get("x-uw-daily-req-count")
            daily_limit = resp.headers.get("x-uw-token-req-limit")
            if daily_count and daily_limit:
                usage_pct = round(100 * int(daily_count) / int(daily_limit), 1)
                logger.info(
                    f"UW API usage: {daily_count}/{daily_limit} ({usage_pct}%)",
                    extra={"event_type": "UW_API_USAGE", "component": "iv_rank"},
                )

            return resp.json()
        except Exception as e:
            logger.warning(f"Failed to fetch IV rank for {ticker}: {e}")
            return None

    async def fetch_and_store(self, tickers: List[str]) -> int:
        """Fetch IV rank for multiple tickers and store."""
        stored = 0
        now = datetime.now(timezone.utc)

        for ticker in tickers:
            data = await asyncio.to_thread(self._fetch_iv_rank, ticker)
            if not data or "data" not in data:
                continue

            iv_data = data["data"]
            if not iv_data:
                continue

            # Handle both list and dict responses from UW API
            if isinstance(iv_data, list):
                iv_data = iv_data[0] if iv_data else {}

            if not isinstance(iv_data, dict):
                logger.warning(f"Unexpected iv_data type for {ticker}: {type(iv_data)}")
                continue

            record = {
                "ticker": ticker,
                "ts_utc": now,
                "iv_rank": float(iv_data.get("iv_rank") or 0),
                "iv_percentile": float(iv_data.get("iv_percentile") or 0),
                "current_iv": float(iv_data.get("current_iv") or 0),
                "iv_52w_high": float(iv_data.get("iv_high") or 0),
                "iv_52w_low": float(iv_data.get("iv_low") or 0),
                "iv_30d": float(iv_data.get("iv_30d") or 0),
            }

            await self._persist_iv_rank(record)
            stored += 1
            await asyncio.sleep(0.5)  # Rate limit

        return stored

    async def _persist_iv_rank(self, record: Dict[str, Any]) -> None:
        """Persist IV rank to database."""

        async def write(session: Any) -> None:
            stmt = text(
                """
                INSERT INTO silver_iv_rank (
                    ticker, ts_utc, iv_rank, iv_percentile,
                    current_iv, iv_52w_high, iv_52w_low, iv_30d
                ) VALUES (
                    :ticker, :ts_utc, :iv_rank, :iv_percentile,
                    :current_iv, :iv_52w_high, :iv_52w_low, :iv_30d
                )
            """
            )
            await session.execute(stmt, record)

        await db_write(write)
