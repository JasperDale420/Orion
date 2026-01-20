"""
VIX/VVIX Data Connector.

Fetches VIX and VVIX data and classifies volatility regime.
"""

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from sqlalchemy import text

from orion.shared.db_utils import db_write

logger = logging.getLogger(__name__)


def classify_vix_regime(vix: float) -> str:
    """Classify VIX level into regime."""
    if vix < 15:
        return "LOW"
    elif vix < 20:
        return "NORMAL"
    elif vix < 30:
        return "ELEVATED"
    else:
        return "EXTREME"


class VIXConnector:
    """Fetches VIX and VVIX data from Alpaca."""

    def __init__(self, api_key: str, api_secret: str):
        self.client = StockHistoricalDataClient(api_key, api_secret)

    def fetch_vix_bars(self, start_date: date, end_date: date) -> List[Dict[str, Any]]:
        """Fetch VIX daily bars."""
        try:
            request = StockBarsRequest(
                symbol_or_symbols=["VIX"],
                timeframe=TimeFrame.Day,
                start=datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc),
                end=datetime.combine(end_date, datetime.max.time()).replace(tzinfo=timezone.utc),
            )
            bars = self.client.get_stock_bars(request)

            result = []
            if bars.data and "VIX" in bars.data:
                for bar in bars.data["VIX"]:
                    result.append(
                        {
                            "ts": bar.timestamp,
                            "close": bar.close,
                            "high": bar.high,
                            "low": bar.low,
                        }
                    )
            return result
        except Exception as e:
            logger.warning(f"Failed to fetch VIX bars: {e}")
            return []

    def fetch_vvix_bars(self, start_date: date, end_date: date) -> List[Dict[str, Any]]:
        """Fetch VVIX daily bars."""
        try:
            request = StockBarsRequest(
                symbol_or_symbols=["VVIX"],
                timeframe=TimeFrame.Day,
                start=datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc),
                end=datetime.combine(end_date, datetime.max.time()).replace(tzinfo=timezone.utc),
            )
            bars = self.client.get_stock_bars(request)

            result = []
            if bars.data and "VVIX" in bars.data:
                for bar in bars.data["VVIX"]:
                    result.append(
                        {
                            "ts": bar.timestamp,
                            "close": bar.close,
                        }
                    )
            return result
        except Exception as e:
            logger.warning(f"Failed to fetch VVIX bars: {e}")
            return []

    async def fetch_and_store(self, start_date: date, end_date: date) -> int:
        """Fetch VIX/VVIX and store with regime classification."""
        vix_bars = await asyncio.to_thread(self.fetch_vix_bars, start_date, end_date)
        vvix_bars = await asyncio.to_thread(self.fetch_vvix_bars, start_date, end_date)

        # Index VVIX by date
        vvix_by_date = {b["ts"].date(): b["close"] for b in vvix_bars}

        # Calculate 5-day MA
        vix_values = [b["close"] for b in vix_bars]

        stored = 0
        for i, bar in enumerate(vix_bars):
            vix = bar["close"]
            ts = bar["ts"]

            # Calculate 1d change
            vix_1d_change = None
            if i > 0:
                prev_vix = vix_bars[i - 1]["close"]
                vix_1d_change = ((vix - prev_vix) / prev_vix) * 100

            # Calculate 5d MA
            vix_5d_ma = None
            if i >= 4:
                vix_5d_ma = sum(vix_values[i - 4 : i + 1]) / 5

            # VVIX lookup
            vvix = vvix_by_date.get(ts.date())

            # Regime
            regime = classify_vix_regime(vix)

            record = {
                "ts_utc": ts,
                "vix": vix,
                "vvix": vvix,
                "vix_1d_change": vix_1d_change,
                "vix_5d_ma": vix_5d_ma,
                "vix_regime": regime,
            }

            await self._persist(record)
            stored += 1

        return stored

    async def _persist(self, record: Dict[str, Any]) -> None:
        """Persist VIX record to database."""

        async def write(session: Any) -> None:
            stmt = text(
                """
                INSERT INTO silver_vix_data (ts_utc, vix, vvix, vix_1d_change, vix_5d_ma, vix_regime)
                VALUES (:ts_utc, :vix, :vvix, :vix_1d_change, :vix_5d_ma, :vix_regime)
                ON CONFLICT DO NOTHING
            """
            )
            await session.execute(stmt, record)

        await db_write(write)
