"""
VIX Proxy Connector.

Uses Heber bar data from VIX-tracking ETFs to compute VIX-like metrics.
Tries multiple VIX proxy symbols in priority order: VIXY (1x short-term VIX futures),
UVIX (2x leveraged short-term VIX futures), VIXM (mid-term VIX futures).
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from orion.clients.heber_reader import get_heber_reader
from orion.shared.dataframe_utils import first_existing_column as _first_existing_column
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.connectors.vix_proxy")


# VIX proxy candidates in priority order: (symbol, multiplier_to_approximate_vix)
# VIXY ~= 0.5 * VIX → multiplier 2.0
# UVIX is 2x leveraged VIX short-term futures; price decays due to leverage but
# rough approximation: UVIX ~= 0.35 * VIX → multiplier ~2.85
# VIXM tracks mid-term VIX futures; price ~= 0.8 * VIX → multiplier ~1.25
_VIX_PROXY_CANDIDATES: list[tuple[str, float]] = [
    ("VIXY", 2.0),
    ("UVIX", 2.85),
    ("VIXM", 1.25),
]


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
    """Computes VIX proxy from VIX-tracking ETF bars sourced from Heber."""

    def __init__(self) -> None:
        self._latest_vix_snapshot: dict[str, Any] | None = None

    async def fetch_and_store(self) -> int:
        """Fetch recent VIX proxy bars and compute VIX proxy metrics."""
        proxy_data, multiplier = await self._get_vix_proxy_bars()
        if not proxy_data:
            logger.warning("No VIX proxy data available (tried %s)", [c[0] for c in _VIX_PROXY_CANDIDATES])
            return 0

        stored = 0
        for i, bar in enumerate(proxy_data):
            proxy_close = bar["close"]
            ts = bar["ts"]

            # Approximate VIX from proxy ETF
            vix_approx = proxy_close * multiplier

            # Calculate 1d change
            vix_1d_change = None
            if i > 0:
                prev_close = proxy_data[i - 1]["close"]
                prev_vix = prev_close * multiplier
                if prev_vix > 0:
                    vix_1d_change = ((vix_approx - prev_vix) / prev_vix) * 100

            # Calculate 5d MA
            vix_5d_ma = None
            if i >= 4:
                vix_values = [v["close"] * multiplier for v in proxy_data[i - 4 : i + 1]]
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
                "proxy_symbol": bar["symbol"],
            }

            await self._persist(record)
            stored += 1

        return stored

    async def _get_vix_proxy_bars(self) -> tuple[list[dict[str, Any]], float]:
        """Try each VIX proxy candidate until one returns data."""
        for symbol, multiplier in _VIX_PROXY_CANDIDATES:
            bars = await self._get_bars_for_symbol(symbol)
            if bars:
                logger.info(
                    "VIX proxy using %s (%d daily bars, multiplier=%.2f)",
                    symbol,
                    len(bars),
                    multiplier,
                )
                return bars, multiplier
            logger.debug("No recent bars for VIX proxy candidate %s", symbol)
        return [], 1.0

    async def _get_bars_for_symbol(self, symbol: str) -> list[dict[str, Any]]:
        """Get recent daily closes for a symbol from Heber minute bars."""
        now = datetime.now(UTC)
        start = now - timedelta(days=60)
        try:
            bars_df = await asyncio.to_thread(
                get_heber_reader().read_bars,
                symbols=[symbol],
                start_time=start,
                asof_time=now,
            )
        except Exception as e:
            logger.error("Failed to fetch %s bars: %s", symbol, e)
            return []

        if bars_df is None or bars_df.empty:
            return []

        ts_col = _first_existing_column(bars_df, ["bar_start_ts_utc", "bar_start_ts", "ts_event", "ts_utc"])
        close_col = _first_existing_column(bars_df, ["close", "c"])
        if ts_col is None or close_col is None:
            return []

        normalized = _coerce_ticker_column(bars_df.copy())
        if "ticker" in normalized.columns:
            normalized = normalized[normalized["ticker"].astype(str).str.upper() == symbol.upper()]
        if normalized.empty:
            return []

        ts_series = pd.to_datetime(normalized[ts_col], utc=True, errors="coerce")
        close_series = pd.to_numeric(normalized[close_col], errors="coerce")
        temp = pd.DataFrame({"ts": ts_series, "close": close_series}).dropna(subset=["ts", "close"])
        if temp.empty:
            return []

        # Only use bars from the last 10 days to avoid stale data
        recent_cutoff = now - timedelta(days=10)
        temp = temp[temp["ts"] >= pd.Timestamp(recent_cutoff)]
        if temp.empty:
            return []

        temp = temp.sort_values("ts")
        temp["day"] = temp["ts"].dt.date
        temp = temp.groupby("day", as_index=False).last().tail(30)
        return [
            {
                "ts": row["ts"].to_pydatetime() if isinstance(row["ts"], pd.Timestamp) else row["ts"],
                "close": float(row["close"]),
                "symbol": symbol,
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
