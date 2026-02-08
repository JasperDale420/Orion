"""
Window Feature Job.

Aggregates flow activity into time-windowed features (5m, 1h, 1d, 1w).
Populates gold_feature_windows table for ML features and analysis.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from orion.config import system_settings
from orion.core.logging_config import setup_logging
from orion.shared.db_utils import db_query, db_write
from sqlalchemy import text

logger = logging.getLogger("orion.jobs.window_feature_job")

# Time buckets aligned with rollup_builder: 5m, 1h, 1d, 1w
WINDOW_PERIODS = {
    "5m": timedelta(minutes=5),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
}

FEATURE_SET_ID = "v1"  # Version the feature schema


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
    ):
        self.tickers = list(tickers) if tickers else list(system_settings.static_watchlist)
        self.periods = periods if periods else list(WINDOW_PERIODS.keys())
        self.loop_interval_seconds = loop_interval_seconds

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
        """Query silver tables and aggregate features for the window."""

        async def query(session: Any) -> Dict[str, Any] | None:
            # Aggregate flow metrics from silver_uw_flow
            flow_stmt = text(
                """
                SELECT
                    COUNT(*) as flow_count,
                    SUM(CASE WHEN put_call = 'C' THEN premium_usd ELSE 0 END) as call_premium,
                    SUM(CASE WHEN put_call = 'P' THEN premium_usd ELSE 0 END) as put_premium,
                    SUM(premium_usd) as total_premium,
                    COUNT(CASE WHEN is_sweep::text = 'true' OR is_sweep::text = 'True' THEN 1 END) as sweep_count,
                    COUNT(CASE WHEN aggressor = 'ASK' THEN 1 END) as ask_side_count,
                    COUNT(CASE WHEN aggressor = 'BID' THEN 1 END) as bid_side_count,
                    AVG(iv) as avg_iv,
                    MAX(premium_usd) as max_premium
                FROM silver_uw_flow
                WHERE ticker = :ticker
                AND flow_ts_utc >= :start_ts
                AND flow_ts_utc < :end_ts
            """
            )
            flow_result = await session.execute(
                flow_stmt,
                {"ticker": ticker, "start_ts": window_start, "end_ts": window_end},
            )
            flow_row = flow_result.fetchone()

            # Aggregate dark pool from silver_uw_darkpool
            dp_stmt = text(
                """
                SELECT
                    COUNT(*) as dp_count,
                    SUM(size_shares) as dp_volume,
                    SUM(size_shares * trade_price) as dp_notional
                FROM silver_uw_darkpool
                WHERE ticker = :ticker
                AND dark_ts_utc >= :start_ts
                AND dark_ts_utc < :end_ts
            """
            )
            dp_result = await session.execute(
                dp_stmt,
                {"ticker": ticker, "start_ts": window_start, "end_ts": window_end},
            )
            dp_row = dp_result.fetchone()

            if not flow_row or flow_row[0] == 0:
                # No flow data in window
                return None

            # Calculate derived features
            flow_count = flow_row[0] or 0
            call_premium = float(flow_row[1] or 0)
            put_premium = float(flow_row[2] or 0)
            total_premium = float(flow_row[3] or 0)
            sweep_count = int(flow_row[4] or 0)
            ask_side = int(flow_row[5] or 0)
            _ = flow_row[6]  # bid_side - available but not currently used
            avg_iv = float(flow_row[7]) if flow_row[7] else None
            max_premium = float(flow_row[8] or 0)

            dp_count = int(dp_row[0] or 0) if dp_row else 0
            dp_volume = float(dp_row[1] or 0) if dp_row else 0
            dp_notional = float(dp_row[2] or 0) if dp_row else 0

            # Derived ratios
            call_put_ratio = call_premium / put_premium if put_premium > 0 else None
            call_put_imbalance = (call_premium - put_premium) / total_premium if total_premium > 0 else 0
            sweep_ratio = sweep_count / flow_count if flow_count > 0 else 0
            ask_ratio = ask_side / flow_count if flow_count > 0 else 0.5

            return {
                # Raw counts
                "flow_count": flow_count,
                "sweep_count": sweep_count,
                "dp_count": dp_count,
                # Premiums
                "call_premium": call_premium,
                "put_premium": put_premium,
                "total_premium": total_premium,
                "max_premium": max_premium,
                # Dark pool
                "dp_volume": dp_volume,
                "dp_notional": dp_notional,
                # Ratios
                "call_put_ratio": call_put_ratio,
                "call_put_imbalance": call_put_imbalance,
                "sweep_ratio": sweep_ratio,
                "ask_ratio": ask_ratio,
                # IV
                "avg_iv": avg_iv,
                # Window metadata
                "period": period,
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
            }

        return await db_query(query)

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


if __name__ == "__main__":
    setup_logging()
    asyncio.run(WindowFeatureJob().run_forever())
