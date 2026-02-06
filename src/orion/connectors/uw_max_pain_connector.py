"""
UW Max Pain Connector.

Fetches max pain strike levels by expiry via Data Gateway.
"""

import asyncio
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import requests
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential

from orion.config import system_settings
from orion.shared.db_utils import db_query, db_write

logger = logging.getLogger(__name__)


class UWMaxPainConnector:
    """Fetches max pain strikes via Data Gateway."""

    def __init__(self, gateway_url: Optional[str] = None, gateway_key: Optional[str] = None):
        self.gateway_url = gateway_url or system_settings.data_gateway_url
        self.gateway_key = gateway_key or system_settings.data_gateway_api_key
        self.headers = {"X-Gateway-Key": self.gateway_key} if self.gateway_key else {}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _fetch_max_pain(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Fetch max pain for a ticker via Data Gateway."""
        url = f"{self.gateway_url}/api/v1/uw/{ticker}/max-pain"
        try:
            resp = requests.get(url, headers=self.headers, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"Failed to fetch max pain for {ticker}: {e}")
            return None

    async def fetch_and_store(self, tickers: List[str]) -> int:
        """Fetch max pain for multiple tickers and store."""
        stored = 0
        today = date.today()

        for ticker in tickers:
            data = await asyncio.to_thread(self._fetch_max_pain, ticker)
            if not data or "data" not in data:
                continue

            expiries = data["data"]
            if not expiries:
                continue

            # Get current price from database (more reliable than API)
            current_price = await self._get_current_price(ticker)

            for exp_data in expiries:
                expiry_str = exp_data.get("expiry")
                max_pain = exp_data.get("max_pain")
                price = exp_data.get("price") or current_price

                if not expiry_str or max_pain is None:
                    continue

                try:
                    expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
                except Exception:
                    continue

                distance_pct = None
                if price and float(price) > 0:
                    distance_pct = ((float(max_pain) - float(price)) / float(price)) * 100

                record = {
                    "ticker": ticker,
                    "expiry": expiry,
                    "date": today,
                    "max_pain_strike": float(max_pain),
                    "current_price": float(price) if price else None,
                    "distance_to_max_pain_pct": distance_pct,
                }

                await self._persist_max_pain(record)
                stored += 1

            await asyncio.sleep(0.5)  # Rate limit

        return stored

    async def _get_current_price(self, ticker: str) -> Optional[float]:
        """Get latest price from silver_alpaca_bars."""

        async def query(session: Any) -> Optional[float]:
            stmt = text(
                """
                SELECT close FROM silver_alpaca_bars
                WHERE ticker = :ticker
                ORDER BY bar_start_ts_utc DESC LIMIT 1
            """
            )
            result = await session.execute(stmt, {"ticker": ticker})
            row = result.fetchone()
            return float(row[0]) if row else None

        return await db_query(query)

    async def _persist_max_pain(self, record: Dict[str, Any]) -> None:
        """Persist max pain to database."""

        async def write(session: Any) -> None:
            stmt = text(
                """
                INSERT INTO silver_max_pain (
                    ticker, expiry, date, max_pain_strike, current_price, distance_to_max_pain_pct
                ) VALUES (
                    :ticker, :expiry, :date, :max_pain_strike, :current_price, :distance_to_max_pain_pct
                )
                ON CONFLICT (ticker, expiry, date) DO UPDATE SET
                    max_pain_strike = EXCLUDED.max_pain_strike,
                    current_price = EXCLUDED.current_price,
                    distance_to_max_pain_pct = EXCLUDED.distance_to_max_pain_pct
            """
            )
            await session.execute(stmt, record)

        await db_write(write)
