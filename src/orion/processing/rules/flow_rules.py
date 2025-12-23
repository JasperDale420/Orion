from typing import Optional

from orion.processing.rules.base import TradingRule
from orion.storage.models_gold import CandidateTrade, TradeDirection
from orion.storage.models_silver import SilverSignal


class BullishSweepRule(TradingRule):
    """
    PRD 9.1 Step 1: Bullish Sweep + Confirming Dark
    - large call sweep (premium >= X)
    - aggressor=ASK (or price >= mid)
    - DTE 7-30d
    - delta in [0.3, 0.6] (if available)
    - concurrent dark pool prints (simplified for v1: just check flow)
    """

    def __init__(self, min_premium: float = 10000.0):
        super().__init__(rule_id="rule_bullish_sweep_v1")
        self.min_premium = min_premium

    def evaluate(self, signal: SilverSignal) -> Optional[CandidateTrade]:
        if signal.signal_type != "UW_FLOW":
            return None

        # Logic: Check if signal is a Flow event with specific characteristics
        # Signal 'features' dict holds normalized fields from Silver

        feat = signal.features
        if not feat:
            return None

        # 1. Filter for UW Flows only (v1 simplification)
        # Assuming our Feature Engine passes raw flow params in 'features' for now
        # or we look at specific columns if it's a vector.
        # For this slice, we assume 'features' contains the raw-ish columns
        # or we'd need to fetch the underlying event.
        # Let's assume FeatureEngine passes 'meta' or fields directly.

        # Check basic criteria
        # "sweep" flag usually passed
        is_sweep = feat.get("is_sweep", False)
        if not is_sweep:
            return None

        # Call vs Put
        if feat.get("put_call") != "CALL":
            return None

        # Premium Size
        premium = feat.get("premium", 0)
        if premium < self.min_premium:
            return None

        # Aggressor
        aggressor = feat.get("aggressor_ind") or feat.get("aggressor") or ""
        if aggressor not in ["ASK", "ABOVE_ASK"]:
            # relaxed check: or price >= mid logic if available
            return None

        # DTE
        dte = feat.get("dte", 0)
        if not (7 <= dte <= 30):
            return None

        # Delta (optional)
        delta = feat.get("delta")
        if delta is not None:
            if not (0.3 <= abs(delta) <= 0.6):
                return None

        candidate = self._create_candidate(
            signal=signal,
            direction=TradeDirection.LONG.value,
            confidence=0.7,
            evidence_extras={
                "event_ids": [feat.get("event_id")] if feat.get("event_id") else [],
                "source_event_id": feat.get("source_event_id"),
                "premium": premium,
                "dte": dte,
                "reason": "Bullish Sweep confirmed",
            },
        )
        candidate.source = "UW"
        candidate.execution_params = {"limit_price": feat.get("underlying_price")}
        return candidate


class BearishPutPressureRule(TradingRule):
    """
    PRD 9.1: Bearish Put Pressure
    - put premium burst
    - aggressor=ASK on puts
    - short DTE (e.g. < 14d)
    """

    def __init__(self, min_premium: float = 10000.0):
        super().__init__(rule_id="rule_bearish_put_pressure_v1")
        self.min_premium = min_premium

    def evaluate(self, signal: SilverSignal) -> Optional[CandidateTrade]:
        if signal.signal_type != "UW_FLOW":
            return None

        feat = signal.features
        if not feat:
            return None

        # Call vs Put
        if feat.get("put_call") != "PUT":
            return None

        # Premium
        premium = feat.get("premium", 0)
        if premium < self.min_premium:
            return None

        # Aggressor (buying puts = bearish)
        aggressor = feat.get("aggressor_ind") or feat.get("aggressor") or ""
        if aggressor not in ["ASK", "ABOVE_ASK"]:
            return None

        # DTE: Short term
        dte = feat.get("dte", 999)
        if dte > 14:
            return None

        candidate = self._create_candidate(
            signal=signal,
            direction=TradeDirection.SHORT.value,
            confidence=0.65,
            evidence_extras={
                "event_ids": [feat.get("event_id")] if feat.get("event_id") else [],
                "source_event_id": feat.get("source_event_id"),
                "premium": premium,
                "dte": dte,
            },
        )
        candidate.source = "UW"
        candidate.execution_params = {"limit_price": feat.get("underlying_price")}
        return candidate
