"""
VIX Proxy Connector.

Uses VIXY price data from silver_alpaca_bars to compute VIX-like metrics.
VIXY closely tracks short-term VIX futures, allowing us to derive a VIX proxy.
"""

import logging
from typing import Any, Dict, Optional

from sqlalchemy import text

from orion.shared.db_utils import db_query, db_write

logger = logging.getLogger(__name__)


# VIX approximation from VIXY: VIXY ~= 0.5 * VIX for day-to-day correlation
# This is a rough proxy; more sophisticated models use VIX futures term structure
VIXY_TO_VIX_MULTIPLIER = 2.0


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


class VIXProxyConnector:
    """Computes VIX proxy from VIXY bars stored in silver_alpaca_bars."""

    async def fetch_and_store(self) -> int:
        """Fetch recent VIXY bars and compute VIX proxy metrics."""
        vixy_data = await self._get_vixy_bars()
        if not vixy_data:
            logger.warning("No VIXY data available for VIX proxy computation")
            return 0

        stored = 0
        for i, bar in enumerate(vixy_data):
            vixy_close = bar["close"]
            ts = bar["ts"]

            # Approximate VIX from VIXY
            vix_approx = vixy_close * VIXY_TO_VIX_MULTIPLIER

            # Calculate 1d change
            vix_1d_change = None
            if i > 0:
                prev_vixy = vixy_data[i - 1]["close"]
                prev_vix = prev_vixy * VIXY_TO_VIX_MULTIPLIER
                if prev_vix > 0:
                    vix_1d_change = ((vix_approx - prev_vix) / prev_vix) * 100

            # Calculate 5d MA
            vix_5d_ma = None
            if i >= 4:
                vix_values = [v["close"] * VIXY_TO_VIX_MULTIPLIER for v in vixy_data[i - 4 : i + 1]]
                vix_5d_ma = sum(vix_values) / 5

            # Regime classification
            regime = classify_vix_regime(vix_approx)

            record = {
                "ts_utc": ts,
                "vix": vix_approx,
                "vvix": None,  # VVIX not available via proxy
                "vix_1d_change": vix_1d_change,
                "vix_5d_ma": vix_5d_ma,
                "vix_regime": regime,
            }

            await self._persist(record)
            stored += 1

        return stored

    async def _get_vixy_bars(self) -> list[Dict[str, Any]]:
        """Get recent VIXY daily bars from silver_alpaca_bars."""

        async def query(session: Any) -> list[Dict[str, Any]]:
            stmt = text(
                """
                SELECT bar_start_ts_utc as ts, close
                FROM silver_alpaca_bars
                WHERE ticker = 'VIXY'
                ORDER BY bar_start_ts_utc DESC
                LIMIT 30
            """
            )
            result = await session.execute(stmt)
            rows = result.fetchall()
            # Return in chronological order
            return [{"ts": r[0], "close": float(r[1])} for r in reversed(rows)]

        try:
            return await db_query(query)
        except Exception as e:
            logger.error(f"Failed to fetch VIXY bars: {e}")
            return []

    async def _persist(self, record: Dict[str, Any]) -> None:
        """Persist VIX proxy record to database."""

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

    async def get_current_vix(self) -> Optional[Dict[str, Any]]:
        """Get the most recent VIX proxy value."""

        async def query(session: Any) -> Optional[Dict[str, Any]]:
            stmt = text(
                """
                SELECT vix, vix_1d_change, vix_regime
                FROM silver_vix_data
                ORDER BY ts_utc DESC
                LIMIT 1
            """
            )
            result = await session.execute(stmt)
            row = result.fetchone()
            if row:
                return {
                    "vix": row[0],
                    "vix_1d_change": row[1],
                    "vix_regime": row[2],
                }
            return None

        try:
            return await db_query(query)
        except Exception:
            return None
