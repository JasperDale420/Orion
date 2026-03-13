"""
VIX Proxy Connector.

Uses Heber VIXY bar data to compute VIX-like metrics.
VIXY closely tracks short-term VIX futures, allowing us to derive a VIX proxy.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from orion.clients.heber_reader import get_heber_reader
from orion.shared.dataframe_utils import first_existing_column as _first_existing_column

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
    """Computes VIX proxy from VIXY bars sourced from Heber."""

    def __init__(self) -> None:
        self._latest_vix_snapshot: dict[str, Any] | None = None

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

    async def _get_vixy_bars(self) -> list[dict[str, Any]]:
        """Get recent VIXY daily closes from Heber minute bars."""
        now = datetime.now(UTC)
        start = now - timedelta(days=60)
        try:
            bars_df = await asyncio.to_thread(
                get_heber_reader().read_bars,
                symbols=["VIXY"],
                start_time=start,
                asof_time=now,
            )
        except Exception as e:
            logger.error(f"Failed to fetch VIXY bars: {e}")
            return []

        if bars_df is None or bars_df.empty:
            return []

        ts_col = _first_existing_column(bars_df, ["bar_start_ts_utc", "bar_start_ts", "ts_event", "ts_utc"])
        close_col = _first_existing_column(bars_df, ["close", "c"])
        if ts_col is None or close_col is None:
            return []

        normalized = _coerce_ticker_column(bars_df.copy())
        if "ticker" in normalized.columns:
            normalized = normalized[normalized["ticker"].astype(str).str.upper() == "VIXY"]
        if normalized.empty:
            return []

        ts_series = pd.to_datetime(normalized[ts_col], utc=True, errors="coerce")
        close_series = pd.to_numeric(normalized[close_col], errors="coerce")
        temp = pd.DataFrame({"ts": ts_series, "close": close_series}).dropna(subset=["ts", "close"])
        if temp.empty:
            return []

        temp = temp.sort_values("ts")
        temp["day"] = temp["ts"].dt.date
        temp = temp.groupby("day", as_index=False).last().tail(30)
        return [
            {
                "ts": row["ts"].to_pydatetime() if isinstance(row["ts"], pd.Timestamp) else row["ts"],
                "close": float(row["close"]),
            }
            for _, row in temp.iterrows()
        ]

    async def _persist(self, record: dict[str, Any]) -> None:
        """Persist the latest computed VIX proxy snapshot in process memory."""
        self._latest_vix_snapshot = dict(record)

    async def get_current_vix(self) -> dict[str, Any] | None:
        """Get the most recent VIX proxy value."""
        if self._latest_vix_snapshot is None:
            return None
        return {
            "vix": self._latest_vix_snapshot.get("vix"),
            "vix_1d_change": self._latest_vix_snapshot.get("vix_1d_change"),
            "vix_regime": self._latest_vix_snapshot.get("vix_regime"),
        }


def _coerce_ticker_column(df: pd.DataFrame) -> pd.DataFrame:
    if "ticker" in df.columns:
        return df
    if "symbol" in df.columns:
        return df.assign(ticker=df["symbol"].astype(str).str.upper())
    if "instrument_key" in df.columns:
        return df.assign(ticker=df["instrument_key"].astype(str).str.split(":").str[-1].str.upper())
    return df
