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

    def __init__(self, vol_window: int = 20, trend_window: int = 50):
        self.vol_window = vol_window
        self.trend_window = trend_window

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

        # Determine Vol State (Thresholds are arbitrary for V1, should be config)
        # Using placeholder check - assumes daily data or similar scale implies high vol > 1.5%
        is_high_vol = realized_vol > 0.015

        # 2. Trend
        if len(data) >= self.trend_window:
            ma_short = data.rolling(window=20).mean().iloc[-1]
            ma_long = data.rolling(window=50).mean().iloc[-1]

            if ma_short > ma_long * 1.01:
                return MarketRegime.TRENDING_UP
            elif ma_short < ma_long * 0.99:
                return MarketRegime.TRENDING_DOWN

        if is_high_vol:
            return MarketRegime.HIGH_VOL

        return MarketRegime.LOW_VOL

    async def get_current_regime_for_ticker(self, ticker: str) -> MarketRegime:
        """
        Fetches recent daily bars from GoldTickerRollup and detects regime.
        """
        async with async_session_factory() as session:
            # Fetch last 60 daily bars (enough for 50 trend)
            stmt = (
                select(GoldTickerRollup)
                .where(GoldTickerRollup.ticker == ticker)
                .where(GoldTickerRollup.period == "1d")
                .order_by(GoldTickerRollup.timestamp_utc.desc())
                .limit(60)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()

            if not rows:
                return MarketRegime.UNKNOWN

            # Sort ascending for pandas
            rows = sorted(rows, key=lambda x: x.timestamp_utc)

            df = pd.DataFrame([{"close": r.close, "timestamp": r.timestamp_utc} for r in rows])

            return self.detect_regime(df)
