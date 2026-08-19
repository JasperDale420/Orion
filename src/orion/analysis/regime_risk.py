"""
Regime-Based Risk Manager.

Applies position sizing multipliers based on multi-axis regime state.
"""

import logging
from pathlib import Path
from typing import Any

import yaml

from orion.analysis.regime import (
    MarketRegimeSnapshot,
    RiskRegime,
    SessionRegime,
    VIXRegime,
    VolRegime,
    is_trusted_vix_source,
)

logger = logging.getLogger(__name__)

# Load config. Four parents from src/orion/analysis/regime_risk.py reaches
# the repo root, where config/regime_risk.yaml actually lives (three parents
# lands in src/ instead — silently returning {} and defaulting every
# multiplier to 1.0 since this module's original commit, 2026-01-02).
CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "regime_risk.yaml"


def _load_regime_risk_config() -> dict[str, Any]:
    """Load regime risk configuration."""
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH) as f:
                return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"Failed to load regime_risk.yaml: {e}")
    return {}


_CONFIG = _load_regime_risk_config()


class RegimeRiskManager:
    """
    Applies regime-based risk adjustments to position sizing and confidence.

    Uses multipliers from config/regime_risk.yaml:
    - vol_multipliers: LOW/NORMAL/HIGH/SHOCK
    - risk_multipliers: RISK_ON/NEUTRAL/RISK_OFF
    - vix_multipliers: LOW/NORMAL/ELEVATED/EXTREME
    - session_multipliers: PREMARKET/OPENING/MIDDAY/POWER_HOUR/CLOSE
    """

    def __init__(self):
        self.vol_mult = _CONFIG.get("vol_multipliers", {})
        self.risk_mult = _CONFIG.get("risk_multipliers", {})
        self.vix_mult = _CONFIG.get("vix_multipliers", {})
        self.session_mult = _CONFIG.get("session_multipliers", {})
        self.min_confidence = _CONFIG.get("min_confidence_to_trade", 0.4)

    def get_vol_multiplier(self, regime: VolRegime, vix_trusted: bool = False) -> float:
        """Get position size multiplier for volatility regime.

        A SHOCK classification backed by an untrusted vix source (e.g. the
        VIXY-ETF-price proxy, not a real spot-VIX print) is capped at the
        HIGH-tier multiplier instead of SHOCK's own — SHOCK's multiplier is
        0.0, which would silently zero out sizing (an effective hard block)
        on a signal that hasn't been confirmed.
        """
        if regime == VolRegime.SHOCK and not vix_trusted:
            return self.vol_mult.get(VolRegime.HIGH.value, 1.0)
        return self.vol_mult.get(regime.value, 1.0)

    def get_risk_multiplier(self, regime: RiskRegime) -> float:
        """Get position size multiplier for risk regime."""
        return self.risk_mult.get(regime.value, 1.0)

    def get_vix_multiplier(self, regime: VIXRegime) -> float:
        """Get position size multiplier for VIX regime."""
        return self.vix_mult.get(regime.value, 1.0)

    def get_session_multiplier(self, regime: SessionRegime) -> float:
        """Get position size multiplier for session."""
        return self.session_mult.get(regime.value, 1.0)

    def calculate_combined_multiplier(self, snapshot: MarketRegimeSnapshot) -> float:
        """
        Calculate combined position size multiplier from all axes.

        final_size = base_size * vol_mult * risk_mult * vix_mult * session_mult
        """
        vix_trusted = is_trusted_vix_source(snapshot.vix_source)
        vol_m = self.get_vol_multiplier(snapshot.vol, vix_trusted)
        risk_m = self.get_risk_multiplier(snapshot.risk)
        vix_m = self.get_vix_multiplier(snapshot.vix_regime)
        session_m = self.get_session_multiplier(snapshot.session)

        combined = vol_m * risk_m * vix_m * session_m

        # Log if significantly reduced
        if combined < 0.5:
            logger.info(
                f"Regime risk significantly reduced: combined_mult={combined:.2f} "
                f"(vol={snapshot.vol.value}, risk={snapshot.risk.value}, "
                f"vix={snapshot.vix_regime.value}, session={snapshot.session.value})"
            )

        return combined

    def should_trade(self, snapshot: MarketRegimeSnapshot) -> bool:
        """
        Check if current regime allows trading.

        Returns False if:
        - Vol regime is SHOCK, backed by a trusted vix source
        - Combined multiplier would be 0
        """
        if snapshot.vol == VolRegime.SHOCK and is_trusted_vix_source(snapshot.vix_source):
            return False

        combined = self.calculate_combined_multiplier(snapshot)
        return combined > 0.0

    def adjust_confidence(self, base_confidence: float, snapshot: MarketRegimeSnapshot) -> float:
        """
        Adjust signal confidence based on regime.

        Reduces confidence in adverse regimes.
        """
        adjusted = base_confidence

        # Penalty for high volatility
        if snapshot.vol == VolRegime.HIGH:
            adjusted -= _CONFIG.get("confidence_penalty_high_vol", 0.15)
        elif snapshot.vol == VolRegime.SHOCK:
            adjusted = 0.0  # No trading in shock

        # Penalty for risk-off
        if snapshot.risk == RiskRegime.RISK_OFF:
            adjusted -= _CONFIG.get("confidence_penalty_risk_off", 0.1)

        # VIX extreme penalty
        if snapshot.vix_regime == VIXRegime.EXTREME:
            adjusted *= 0.5

        return max(0.0, min(1.0, adjusted))
