"""
UW Greek Exposure Connector.

Fetches GEX (Gamma), VEX (Vanna), CEX (Charm) exposure data from Unusual Whales API.
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


class UWGreekExposureConnector:
    """Fetches Greek exposure (GEX, Vanna, Charm) from UW API."""

    BASE_URL = "https://api.unusualwhales.com"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {"Authorization": f"Bearer {api_key}"}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _fetch_greek_exposure(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Fetch greek exposure for a ticker."""
        url = f"{self.BASE_URL}/api/stock/{ticker}/greek-exposure"
        try:
            resp = requests.get(url, headers=self.headers, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"Failed to fetch greek exposure for {ticker}: {e}")
            return None

    async def fetch_and_store(self, tickers: List[str]) -> int:
        """Fetch greek exposure for multiple tickers and store."""
        stored = 0
        now = datetime.now(timezone.utc)

        for ticker in tickers:
            data = await asyncio.to_thread(self._fetch_greek_exposure, ticker)
            if not data or "data" not in data:
                continue

            exposure_data = data["data"]
            if not exposure_data:
                continue

            # Handle list response (multiple expiries) or single dict
            if isinstance(exposure_data, list):
                # Sum up all expiries for aggregate GEX
                total_gex_oi = sum(float(e.get("gex_per_one_percent_move_oi") or 0) for e in exposure_data)
                total_gex_vol = sum(float(e.get("gex_per_one_percent_move_volume") or 0) for e in exposure_data)
                total_vex_oi = sum(float(e.get("vex_per_one_percent_move_oi") or 0) for e in exposure_data)
                total_vex_vol = sum(float(e.get("vex_per_one_percent_move_volume") or 0) for e in exposure_data)
                total_cex_oi = sum(float(e.get("cex_per_one_percent_move_oi") or 0) for e in exposure_data)
                total_cex_vol = sum(float(e.get("cex_per_one_percent_move_volume") or 0) for e in exposure_data)
                call_delta = sum(float(e.get("call_delta") or 0) for e in exposure_data)
                put_delta = sum(float(e.get("put_delta") or 0) for e in exposure_data)
                spot = float(exposure_data[0].get("spot_price") or 0) if exposure_data else 0
            else:
                # Single dict response
                total_gex_oi = float(exposure_data.get("gex_per_one_percent_move_oi") or 0)
                total_gex_vol = float(exposure_data.get("gex_per_one_percent_move_volume") or 0)
                total_vex_oi = float(exposure_data.get("vex_per_one_percent_move_oi") or 0)
                total_vex_vol = float(exposure_data.get("vex_per_one_percent_move_volume") or 0)
                total_cex_oi = float(exposure_data.get("cex_per_one_percent_move_oi") or 0)
                total_cex_vol = float(exposure_data.get("cex_per_one_percent_move_volume") or 0)
                call_delta = float(exposure_data.get("call_delta") or 0)
                put_delta = float(exposure_data.get("put_delta") or 0)
                spot = float(exposure_data.get("spot_price") or 0)

            record = {
                "ticker": ticker,
                "ts_utc": now,
                "gex_oi": total_gex_oi,
                "gex_volume": total_gex_vol,
                "vex_oi": total_vex_oi,
                "vex_volume": total_vex_vol,
                "cex_oi": total_cex_oi,
                "cex_volume": total_cex_vol,
                "call_delta": call_delta,
                "put_delta": put_delta,
                "call_fill_delta": 0,
                "put_fill_delta": 0,
                "spot_price": spot,
            }

            await self._persist_exposure(record)
            stored += 1
            await asyncio.sleep(0.5)  # Rate limit

        return stored

    async def _persist_exposure(self, record: Dict[str, Any]) -> None:
        """Persist greek exposure to database."""

        async def write(session: Any) -> None:
            stmt = text(
                """
                INSERT INTO silver_greek_exposure (
                    ticker, ts_utc, gex_oi, gex_volume,
                    vex_oi, vex_volume, cex_oi, cex_volume,
                    call_delta, put_delta, call_fill_delta, put_fill_delta,
                    spot_price
                ) VALUES (
                    :ticker, :ts_utc, :gex_oi, :gex_volume,
                    :vex_oi, :vex_volume, :cex_oi, :cex_volume,
                    :call_delta, :put_delta, :call_fill_delta, :put_fill_delta,
                    :spot_price
                )
            """
            )
            await session.execute(stmt, record)

        await db_write(write)
