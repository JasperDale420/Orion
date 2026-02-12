"""
Window Feature Job.

Aggregates flow activity into time-windowed features (5m, 1h, 1d, 1w).
Populates gold_feature_windows table for ML features and analysis.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pandas as pd
from sqlalchemy import text

from orion.clients.heber_reader import get_heber_reader
from orion.config import system_settings
from orion.core.logging_config import setup_logging
from orion.shared.db_utils import db_write

logger = logging.getLogger("orion.jobs.window_feature_job")

# Time buckets aligned with rollup_builder: 5m, 1h, 1d, 1w
WINDOW_PERIODS = {
    "5m": timedelta(minutes=5),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
}

FEATURE_SET_ID = "v1"  # Version the feature schema
_PREFER_HEBER_FALSE_VALUES = {"0", "false", "no", "off", "n"}


class WindowFeatureJob:
    """
    Builds window-level aggregated features from silver tables.

    For each (ticker, period, window_end), calculates:
    - Flow metrics: call/put premium, sweep count, sweep ratio
    - Dark pool: DP volume, DP notional
    - Sentiment: call/put imbalance, aggressor ratio
    """

    def __init__(
        self,
        *,
        tickers: List[str] | None = None,
        periods: List[str] | None = None,
        loop_interval_seconds: float = 60.0,
        prefer_heber: bool | None = None,
    ):
        self.tickers = list(tickers) if tickers else list(system_settings.static_watchlist)
        self.periods = periods if periods else list(WINDOW_PERIODS.keys())
        self.loop_interval_seconds = loop_interval_seconds
        self.prefer_heber = _prefer_heber_source(prefer_heber)

    async def run_once(self) -> int:
        """Build window features for all tickers and periods. Returns count of rows created."""
        now = datetime.now(timezone.utc)
        total_rows = 0

        for ticker in self.tickers:
            for period_name in self.periods:
                try:
                    window_size = WINDOW_PERIODS[period_name]
                    window_end = now
                    window_start = now - window_size

                    features = await self._build_features(
                        ticker=ticker,
                        window_start=window_start,
                        window_end=window_end,
                        period=period_name,
                    )

                    if features:
                        await self._persist_features(
                            ticker=ticker,
                            window_end=window_end,
                            period=period_name,
                            features=features,
                        )
                        total_rows += 1

                except Exception as e:
                    logger.error(f"Error building window features for {ticker}/{period_name}: {e}")

        logger.info(f"Built {total_rows} window feature rows")
        return total_rows

    async def _build_features(
        self, ticker: str, window_start: datetime, window_end: datetime, period: str
    ) -> Dict[str, Any] | None:
        """Query data sources and aggregate features for the window."""
        if not self.prefer_heber:
            return None
        return await self._build_features_from_heber(
            ticker=ticker,
            window_start=window_start,
            window_end=window_end,
            period=period,
        )

    async def _build_features_from_local_db(
        self, ticker: str, window_start: datetime, window_end: datetime, period: str
    ) -> Dict[str, Any] | None:
        """Legacy local-DB path is disabled in Heber-only mode."""
        _ = (ticker, window_start, window_end, period)
        return None

    async def _build_features_from_heber(
        self, ticker: str, window_start: datetime, window_end: datetime, period: str
    ) -> Dict[str, Any] | None:
        """Read Heber flow/darkpool datasets and aggregate equivalent window features."""
        reader = get_heber_reader()
        try:
            flow_df = await asyncio.to_thread(
                reader.read_flow,
                symbols=[ticker],
                start_time=window_start,
                asof_time=window_end,
            )
            darkpool_df = await asyncio.to_thread(
                reader.read_darkpool,
                symbols=[ticker],
                start_time=window_start,
                asof_time=window_end,
            )
        except Exception as exc:
            logger.warning(
                "window_feature_heber_read_failed",
                extra={
                    "event_type": "WINDOW_FEATURE_HEBER_READ_FAILED",
                    "ticker": ticker,
                    "period": period,
                    "error": str(exc),
                },
            )
            return None

        if flow_df is None or flow_df.empty:
            return None

        flow_df = _coerce_ticker_column(flow_df)
        flow_df = _filter_rows_by_ticker(flow_df, ticker=ticker)
        if flow_df.empty:
            return None

        premium_col = _first_existing_column(flow_df, ["premium_usd", "premium"])
        if premium_col is None:
            return None

        premium_series = pd.to_numeric(flow_df[premium_col], errors="coerce").fillna(0.0)
        put_call_series = flow_df.get("put_call", pd.Series(index=flow_df.index, dtype=str)).astype(str).str.upper()
        sweep_series = _series_to_bool(flow_df.get("is_sweep", pd.Series(False, index=flow_df.index)))
        aggressor_series = flow_df.get("aggressor", pd.Series(index=flow_df.index, dtype=str)).astype(str).str.upper()
        iv_col = _first_existing_column(flow_df, ["iv", "implied_volatility"])
        iv_series = (
            pd.to_numeric(flow_df[iv_col], errors="coerce") if iv_col else pd.Series(index=flow_df.index, dtype=float)
        )

        flow_count = int(len(flow_df))
        call_premium = float(premium_series[put_call_series == "C"].sum())
        put_premium = float(premium_series[put_call_series == "P"].sum())
        total_premium = float(premium_series.sum())
        sweep_count = int(sweep_series.sum())
        ask_side = int((aggressor_series == "ASK").sum())
        avg_iv = float(iv_series.mean()) if not iv_series.dropna().empty else None
        max_premium = float(premium_series.max()) if not premium_series.empty else 0.0

        if darkpool_df is None or darkpool_df.empty:
            dp_count = 0
            dp_volume = 0.0
            dp_notional = 0.0
        else:
            darkpool_df = _coerce_ticker_column(darkpool_df)
            darkpool_df = _filter_rows_by_ticker(darkpool_df, ticker=ticker)
            size_col = _first_existing_column(darkpool_df, ["size_shares", "size"])
            price_col = _first_existing_column(darkpool_df, ["trade_price", "price"])
            size_series = (
                pd.to_numeric(darkpool_df[size_col], errors="coerce").fillna(0.0)
                if size_col
                else pd.Series(0.0, index=darkpool_df.index)
            )
            price_series = (
                pd.to_numeric(darkpool_df[price_col], errors="coerce").fillna(0.0)
                if price_col
                else pd.Series(0.0, index=darkpool_df.index)
            )
            dp_count = int(len(darkpool_df))
            dp_volume = float(size_series.sum())
            dp_notional = float((size_series * price_series).sum())

        call_put_ratio = call_premium / put_premium if put_premium > 0 else None
        call_put_imbalance = (call_premium - put_premium) / total_premium if total_premium > 0 else 0
        sweep_ratio = sweep_count / flow_count if flow_count > 0 else 0
        ask_ratio = ask_side / flow_count if flow_count > 0 else 0.5

        return {
            "flow_count": flow_count,
            "sweep_count": sweep_count,
            "dp_count": dp_count,
            "call_premium": call_premium,
            "put_premium": put_premium,
            "total_premium": total_premium,
            "max_premium": max_premium,
            "dp_volume": dp_volume,
            "dp_notional": dp_notional,
            "call_put_ratio": call_put_ratio,
            "call_put_imbalance": call_put_imbalance,
            "sweep_ratio": sweep_ratio,
            "ask_ratio": ask_ratio,
            "avg_iv": avg_iv,
            "period": period,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        }

    async def _persist_features(self, ticker: str, window_end: datetime, period: str, features: Dict[str, Any]) -> None:
        """Upsert window features to gold_feature_windows."""

        async def write(session: Any) -> None:
            stmt = text(
                """
                INSERT INTO gold_feature_windows (
                    ticker, window_end_ts_utc, period, feature_set_id, features, created_at_utc
                ) VALUES (
                    :ticker, :window_end, :period, :feature_set_id, :features, :created_at
                )
                ON CONFLICT (ticker, window_end_ts_utc, period, feature_set_id) DO UPDATE SET
                    features = EXCLUDED.features,
                    created_at_utc = EXCLUDED.created_at_utc
            """
            )
            await session.execute(
                stmt,
                {
                    "ticker": ticker,
                    "window_end": window_end,
                    "period": period,
                    "feature_set_id": FEATURE_SET_ID,
                    "features": json.dumps(features),
                    "created_at": datetime.now(timezone.utc),
                },
            )

        await db_write(write)

    async def run_forever(self) -> None:
        """Run continuously, building window features on each interval."""
        logger.info(
            "Starting window feature job",
            extra={
                "event_type": "WINDOW_FEATURE_JOB_START",
                "tickers": len(self.tickers),
                "periods": self.periods,
                "interval_seconds": self.loop_interval_seconds,
            },
        )

        while True:
            t0 = asyncio.get_running_loop().time()
            try:
                await self.run_once()
            except Exception as e:
                logger.error(f"Window feature job iteration failed: {e}")

            elapsed = asyncio.get_running_loop().time() - t0
            await asyncio.sleep(max(1.0, self.loop_interval_seconds - elapsed))


def _prefer_heber_source(prefer_heber: bool | None) -> bool:
    if prefer_heber is not None:
        return prefer_heber
    raw = os.getenv("ORION_WINDOW_FEATURE_JOB_PREFER_HEBER", "1").strip().lower()
    return raw not in _PREFER_HEBER_FALSE_VALUES


def _first_existing_column(df: pd.DataFrame, candidates: List[str]) -> str | None:
    for name in candidates:
        if name in df.columns:
            return name
    return None


def _coerce_ticker_column(df: pd.DataFrame) -> pd.DataFrame:
    if "ticker" in df.columns:
        return df
    if "symbol" in df.columns:
        return df.assign(ticker=df["symbol"].astype(str).str.upper())
    if "instrument_key" in df.columns:
        return df.assign(ticker=df["instrument_key"].astype(str).str.split(":").str[-1].str.upper())
    return df


def _filter_rows_by_ticker(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if "ticker" not in df.columns:
        return df
    return df[df["ticker"].astype(str).str.upper() == ticker.upper()]


def _series_to_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return series.astype(str).str.lower().isin({"true", "1", "yes", "y", "t"})


if __name__ == "__main__":
    setup_logging()
    asyncio.run(WindowFeatureJob().run_forever())
