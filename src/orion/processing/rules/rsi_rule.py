from typing import Optional

from orion.processing.rules.base import TradingRule
from orion.storage.models_gold import CandidateTrade, TradeDirection
from orion.storage.models_silver import SilverSignal


class RSIOversoldRule(TradingRule):
    """
    Basic Mean Reversion:
    If RSI_14 < 30, generate LONG candidate.
    """

    def __init__(self):
        super().__init__(rule_id="rsi_oversold_v1")
        self.rsi_threshold = 30.0

    def evaluate(self, signal: SilverSignal) -> Optional[CandidateTrade]:
        # Only evaluate OHLCV signals
        if signal.signal_type != "OHLCV_1M":
            return None

        features = signal.features
        rsi = features.get("RSI_14")

        if rsi is None:
            return None

        if rsi < self.rsi_threshold:
            return self._create_candidate(
                signal=signal,
                direction=TradeDirection.LONG.value,
                confidence=1.0,  # Placeholder confidence
                evidence_extras={"rsi_val": rsi, "threshold": self.rsi_threshold},
            )

        return None
