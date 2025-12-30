import enum
import logging

import numpy as np
import pandas as pd
from sqlalchemy import select

from orion.storage.db import async_session_factory
from orion.storage.models_gold import GoldTickerRollup

logger = logging.getLogger(__name__)


class MarketRegime(str, enum.Enum):
    LOW_VOL = "LOW_VOL"
    HIGH_VOL = "HIGH_VOL"
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class RegimeDetector:
    """
    Detects market regime based on recent price history.
    PRD 5.6.1: Required for Solver Router context.
    """

    def __init__(
        self,
        vol_window: int = 20,
        trend_window: int = 50,
        vol_threshold: float = 0.015,  # L2: configurable volatility threshold
        trend_threshold: float = 0.01,  # L2: configurable trend threshold (1%)
        max_bars: int = 60,  # L3: configurable bar fetch limit
    ):
        self.vol_window = vol_window
        self.trend_window = trend_window
        self.vol_threshold = vol_threshold
        self.trend_threshold = trend_threshold
        self.max_bars = max_bars

    def detect_regime(self, prices: pd.DataFrame) -> MarketRegime:
        """
        Simple heuristic detection.
        prices: DataFrame with 'close' and 'date/index'.
        """
        if prices.empty or len(prices) < self.vol_window:
            return MarketRegime.UNKNOWN

        # 1. Volatility (Annualized std dev of log returns)
        # Using simple close-to-close returns
        data = prices["close"]
        log_rets = np.log(data / data.shift(1))

        # Realized Vol (last N periods)
        realized_vol = log_rets.rolling(window=self.vol_window).std().iloc[-1]

        # Validate volatility calculation (use epsilon for floating point comparison)
        if pd.isna(realized_vol) or abs(realized_vol) < 1e-10:
            logger.warning(
                f"Invalid volatility for regime detection: {realized_vol} (likely insufficient data or flat prices)"
            )
            return MarketRegime.UNKNOWN

        # Determine Vol State (L2: using configurable threshold)
        is_high_vol = realized_vol > self.vol_threshold

        # 2. Trend
        if len(data) >= self.trend_window:
            ma_short = data.rolling(window=20).mean().iloc[-1]
            ma_long = data.rolling(window=50).mean().iloc[-1]

            # Validate moving averages before comparison
            if pd.isna(ma_short) or pd.isna(ma_long):
                logger.debug("Insufficient data for trend calculation (NaN moving averages)")
            elif ma_short > ma_long * (1 + self.trend_threshold):
                return MarketRegime.TRENDING_UP
            elif ma_short < ma_long * (1 - self.trend_threshold):
                return MarketRegime.TRENDING_DOWN

        if is_high_vol:
            return MarketRegime.HIGH_VOL

        return MarketRegime.LOW_VOL

    async def get_current_regime_for_ticker(self, ticker: str) -> MarketRegime:
        """
        Fetches recent daily bars from GoldTickerRollup and detects regime.
        """
        async with async_session_factory() as session:
            # L3: Use configurable max_bars (enough for trend window + buffer)
            stmt = (
                select(GoldTickerRollup)
                .where(GoldTickerRollup.ticker == ticker)
                .where(GoldTickerRollup.period == "1d")
                .order_by(GoldTickerRollup.timestamp_utc.desc())
                .limit(self.max_bars)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()

            if not rows:
                return MarketRegime.UNKNOWN

            # Sort ascending for pandas
            # Create DataFrame for regime detection
            df = pd.DataFrame([{"close": float(r.close), "timestamp": r.timestamp_utc} for r in rows])

            # Explicitly set timezone awareness for pandas (M2 remediation)
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df.set_index("timestamp", inplace=True)

            return self.detect_regime(df)
