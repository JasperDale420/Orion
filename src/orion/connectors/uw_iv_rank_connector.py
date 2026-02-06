"""
UW IV Rank Connector.

Fetches IV rank and percentile via Data Gateway.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential

from orion.config import system_settings
from orion.shared.db_utils import db_write

logger = logging.getLogger(__name__)


class UWIVRankConnector:
    """Fetches IV rank/percentile via Data Gateway."""

    def __init__(self, gateway_url: Optional[str] = None, gateway_key: Optional[str] = None):
        self.gateway_url = gateway_url or system_settings.data_gateway_url
        self.gateway_key = gateway_key or system_settings.data_gateway_api_key
        self.headers = {"X-Gateway-Key": self.gateway_key} if self.gateway_key else {}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _fetch_iv_rank(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Fetch IV rank for a ticker via Data Gateway."""
        url = f"{self.gateway_url}/api/v1/uw/{ticker}/iv-rank"
        try:
            resp = requests.get(url, headers=self.headers, timeout=30)
            resp.raise_for_status()
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
