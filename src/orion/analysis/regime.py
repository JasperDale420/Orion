import enum
from orion.shared.logger import setup_struct_logger

import numpy as np
import pandas as pd
from sqlalchemy import select

from orion.storage.db import async_session_factory
from orion.storage.models_gold import GoldTickerRollup

logger = setup_struct_logger("orion.analysis.regime")


class MarketRegime(str, enum.Enum):
    LOW_VOL = "LOW_VOL"
    HIGH_VOL = "HIGH_VOL"
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


# Multi-axis regime enums (PRD Regime Upgrade)
class TrendRegime(str, enum.Enum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"


class VolRegime(str, enum.Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    SHOCK = "shock"


class RiskRegime(str, enum.Enum):
    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"


class SessionRegime(str, enum.Enum):
    PREMARKET = "premarket"
    OPENING = "opening"  # First 30 min
    MIDDAY = "midday"
    POWER_HOUR = "power_hour"  # Last 60 min
    CLOSE = "close"


class VIXRegime(str, enum.Enum):
    LOW = "low"  # < 15
    NORMAL = "normal"  # 15-20
    ELEVATED = "elevated"  # 20-30
    EXTREME = "extreme"  # > 30


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


# ============================================================================
# Multi-Axis Regime Detector (PRD Regime Upgrade)
# ============================================================================

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional


@dataclass(frozen=True)
class MarketRegimeSnapshot:
    """Complete multi-axis regime state at a point in time."""

    ts: datetime

    # Discrete regimes
    trend: TrendRegime
    vol: VolRegime
    risk: RiskRegime
    session: SessionRegime
    vix_regime: VIXRegime

    # Continuous features
    vix_level: Optional[float] = None
    realized_vol: Optional[float] = None
    trend_strength: Optional[float] = None
    risk_score: Optional[float] = None

    # Confidence per axis (0-1)
    confidence: Dict[str, float] = field(default_factory=dict)


class MultiAxisRegimeDetector:
    """
    Detects multi-axis market regime combining:
    - Price trend (SPY direction)
    - Volatility (realized vol + VIX)
    - Risk sentiment (VIX direction + market tide)
    - Session time
    """

    VIX_THRESHOLDS = {"low": 15, "normal": 20, "elevated": 30}
    VOL_QUANTILES = {"low": 0.33, "high": 0.66, "shock": 0.95}

    def __init__(self, trend_threshold: float = 0.01, vol_window: int = 20):
        self.trend_threshold = trend_threshold
        self.vol_window = vol_window

    def detect_session(self, ts: datetime | str) -> SessionRegime:
        """Classify session based on ET market hours."""
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                ts = datetime.now(timezone.utc)

        # Convert to ET (UTC-5 during EST, UTC-4 during EDT)
        # Approximate: use UTC and offset by 5 hours
        et_hour = (ts.hour - 5) % 24
        et_minute = ts.minute

        market_open = 9 * 60 + 30  # 9:30 AM
        power_hour = 15 * 60  # 3:00 PM
        market_close = 16 * 60  # 4:00 PM

        current_min = et_hour * 60 + et_minute

        if current_min < market_open:
            return SessionRegime.PREMARKET
        elif current_min < market_open + 30:
            return SessionRegime.OPENING
        elif current_min >= power_hour and current_min < market_close:
            return SessionRegime.POWER_HOUR
        elif current_min >= market_close:
            return SessionRegime.CLOSE
        else:
            return SessionRegime.MIDDAY

    def classify_vix_regime(self, vix: Optional[float]) -> VIXRegime:
        """Classify VIX level into regime."""
        if vix is None:
            return VIXRegime.NORMAL
        if vix < self.VIX_THRESHOLDS["low"]:
            return VIXRegime.LOW
        elif vix < self.VIX_THRESHOLDS["normal"]:
            return VIXRegime.NORMAL
        elif vix < self.VIX_THRESHOLDS["elevated"]:
            return VIXRegime.ELEVATED
        else:
            return VIXRegime.EXTREME

    def classify_vol_regime(self, realized_vol: float, vix: Optional[float]) -> VolRegime:
        """Classify volatility regime from realized vol and VIX."""
        # Use VIX as primary if available
        if vix is not None:
            if vix > 35:
                return VolRegime.SHOCK
            elif vix > 25:
                return VolRegime.HIGH
            elif vix < 13:
                return VolRegime.LOW
            else:
                return VolRegime.NORMAL

        # Fall back to realized vol thresholds
        if realized_vol > 0.03:
            return VolRegime.SHOCK
        elif realized_vol > 0.02:
            return VolRegime.HIGH
        elif realized_vol < 0.008:
            return VolRegime.LOW
        else:
            return VolRegime.NORMAL

    def classify_trend_regime(self, cum_ret: float, trend_strength: float) -> TrendRegime:
        """Classify trend from cumulative return and strength."""
        if trend_strength < self.trend_threshold:
            return TrendRegime.FLAT
        elif cum_ret > 0:
            return TrendRegime.UP
        else:
            return TrendRegime.DOWN

    def classify_risk_regime(self, vix_1d_change: Optional[float], market_tide_net: Optional[float]) -> RiskRegime:
        """Classify risk sentiment from VIX direction + market tide."""
        risk_score = 0.0

        # VIX rising = risk off, VIX falling = risk on
        if vix_1d_change is not None:
            if vix_1d_change > 5:  # VIX up > 5%
                risk_score -= 1
            elif vix_1d_change < -5:  # VIX down > 5%
                risk_score += 1

        # Market tide: positive = bullish flow = risk on
        if market_tide_net is not None:
            if market_tide_net > 1_000_000:  # Strong call premium
                risk_score += 1
            elif market_tide_net < -1_000_000:  # Strong put premium
                risk_score -= 1

        if risk_score >= 1:
            return RiskRegime.RISK_ON
        elif risk_score <= -1:
            return RiskRegime.RISK_OFF
        else:
            return RiskRegime.NEUTRAL

    def detect(
        self,
        ts: datetime,
        cum_ret: float = 0.0,
        realized_vol: float = 0.015,
        vix: Optional[float] = None,
        vix_1d_change: Optional[float] = None,
        market_tide_net: Optional[float] = None,
    ) -> MarketRegimeSnapshot:
        """Detect full multi-axis regime snapshot."""
        trend_strength = abs(cum_ret) / (realized_vol + 1e-9)

        return MarketRegimeSnapshot(
            ts=ts,
            trend=self.classify_trend_regime(cum_ret, trend_strength),
            vol=self.classify_vol_regime(realized_vol, vix),
            risk=self.classify_risk_regime(vix_1d_change, market_tide_net),
            session=self.detect_session(ts),
            vix_regime=self.classify_vix_regime(vix),
            vix_level=vix,
            realized_vol=realized_vol,
            trend_strength=trend_strength,
            risk_score=None,
            confidence={
                "trend": 0.8 if trend_strength > 0.5 else 0.5,
                "vol": 0.9 if vix is not None else 0.6,
                "risk": 0.7 if vix_1d_change is not None or market_tide_net is not None else 0.4,
                "session": 1.0,
            },
        )
