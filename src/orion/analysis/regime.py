"""
Market Regime Detection.

Provides multi-axis regime classification combining trend, volatility,
risk sentiment, and session time into a MarketRegimeSnapshot.
"""

import enum
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


class MarketRegime(str, enum.Enum):
    """Legacy single-axis regime. Deprecated — use MarketRegimeSnapshot instead."""

    LOW_VOL = "LOW_VOL"
    HIGH_VOL = "HIGH_VOL"
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


# Multi-axis regime enums
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
    vix_level: float | None = None
    realized_vol: float | None = None
    trend_strength: float | None = None
    risk_score: float | None = None

    # Confidence per axis (0-1)
    confidence: dict[str, float] = field(default_factory=dict)


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
                logger.warning("Failed to parse regime timestamp, falling back to UTC now", exc_info=True)
                ts = datetime.now(UTC)

        # Convert to ET (UTC-5 during EST, UTC-4 during EDT)
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

    def classify_vix_regime(self, vix: float | None) -> VIXRegime:
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

    def classify_vol_regime(self, realized_vol: float, vix: float | None) -> VolRegime:
        """Classify volatility regime from realized vol and VIX."""
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

    def classify_risk_regime(self, vix_1d_change: float | None, market_tide_net: float | None) -> RiskRegime:
        """Classify risk sentiment from VIX direction + market tide."""
        risk_score = 0.0

        if vix_1d_change is not None:
            if vix_1d_change > 5:
                risk_score -= 1
            elif vix_1d_change < -5:
                risk_score += 1

        if market_tide_net is not None:
            if market_tide_net > 1_000_000:
                risk_score += 1
            elif market_tide_net < -1_000_000:
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
        vix: float | None = None,
        vix_1d_change: float | None = None,
        market_tide_net: float | None = None,
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
