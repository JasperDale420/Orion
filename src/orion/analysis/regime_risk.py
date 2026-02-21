"""
Regime-Based Risk Manager.

Applies position sizing multipliers based on multi-axis regime state.
"""

from orion.shared.logger import setup_struct_logger
from pathlib import Path
from typing import Any, Dict

import yaml

from orion.analysis.regime import (
    MarketRegimeSnapshot,
    RiskRegime,
    SessionRegime,
    VIXRegime,
    VolRegime,
)

logger = setup_struct_logger("orion.analysis.regime_risk")

# Load config
CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "regime_risk.yaml"


def _load_regime_risk_config() -> Dict[str, Any]:
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

    def get_vol_multiplier(self, regime: VolRegime) -> float:
        """Get position size multiplier for volatility regime."""
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
        vol_m = self.get_vol_multiplier(snapshot.vol)
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
        - Vol regime is SHOCK
        - Combined multiplier would be 0
        """
        if snapshot.vol == VolRegime.SHOCK:
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
