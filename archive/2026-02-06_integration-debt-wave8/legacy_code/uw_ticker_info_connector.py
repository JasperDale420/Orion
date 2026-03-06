"""
UW Ticker Info Connector.

Fetches ticker information (sector, industry, market cap) from Data Gateway.
Caches results in silver_ticker_info to avoid repeated API calls.
"""

import asyncio
import logging
from typing import Any

import requests
from orion.shared.db_utils import db_query, db_write
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential

from orion.config import system_settings

logger = logging.getLogger(__name__)


class UWTickerInfoConnector:
    """Fetches and caches ticker info via Data Gateway."""

    def __init__(self, gateway_url: str | None = None, gateway_key: str | None = None):
        self.gateway_url = gateway_url or system_settings.data_gateway_url
        self.gateway_key = gateway_key or system_settings.data_gateway_api_key
        self.headers = {"X-Gateway-Key": self.gateway_key} if self.gateway_key else {}
        self._cache: dict[str, dict[str, Any]] = {}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _fetch_ticker_info(self, ticker: str) -> dict[str, Any] | None:
        """Fetch ticker info via Data Gateway."""
        url = f"{self.gateway_url}/api/v1/uw/stock/{ticker}/info"
        try:
            resp = requests.get(url, headers=self.headers, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"Failed to fetch ticker info for {ticker}: {e}")
            return None

    async def get_ticker_info(self, ticker: str) -> dict[str, Any] | None:
        """Get ticker info from cache, database, or API (in that order)."""
        if ticker in self._cache:
            return self._cache[ticker]

        db_info = await self._get_from_db(ticker)
        if db_info:
            self._cache[ticker] = db_info
            return db_info

        api_info = await self._fetch_and_store(ticker)
        if api_info:
            self._cache[ticker] = api_info
            return api_info

        return None

    async def _get_from_db(self, ticker: str) -> dict[str, Any] | None:
        """Check if ticker info exists in database."""

        async def query(session: Any) -> dict[str, Any] | None:
            stmt = text(
                """
                SELECT ticker, company_name, sector, industry, market_cap, exchange
                FROM silver_ticker_info WHERE ticker = :ticker
            """
            )
            result = await session.execute(stmt, {"ticker": ticker})
            row = result.fetchone()
            if row:
                return {
                    "ticker": row[0],
                    "company_name": row[1],
                    "sector": row[2],
                    "industry": row[3],
                    "market_cap": row[4],
                    "exchange": row[5],
                }
            return None

        return await db_query(query)

    async def _fetch_and_store(self, ticker: str) -> dict[str, Any] | None:
        """Fetch from API and store in database."""
        data = await asyncio.to_thread(self._fetch_ticker_info, ticker)
        if not data or "data" not in data:
            return None

        info = data["data"]
        record = {
            "ticker": ticker,
            "company_name": info.get("full_name"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "market_cap": int(info.get("marketcap", 0)) if info.get("marketcap") else None,
            "exchange": info.get("exchange"),
        }

        await self._persist(record)
        return record

    async def _persist(self, record: dict[str, Any]) -> None:
        """Persist ticker info to database."""

        async def write(session: Any) -> None:
            stmt = text(
                """
                INSERT INTO silver_ticker_info (
                    ticker, company_name, sector, industry, market_cap, exchange, last_updated
                ) VALUES (
                    :ticker, :company_name, :sector, :industry, :market_cap, :exchange, NOW()
                )
                ON CONFLICT (ticker) DO UPDATE SET
                    company_name = EXCLUDED.company_name,
                    sector = EXCLUDED.sector,
                    industry = EXCLUDED.industry,
                    market_cap = EXCLUDED.market_cap,
                    exchange = EXCLUDED.exchange,
                    last_updated = NOW()
            """
            )
            await session.execute(stmt, record)

        await db_write(write)
        logger.info(f"Stored ticker info for {record['ticker']}: sector={record.get('sector')}")
