import logging
from typing import Any, Dict, List

from orion.processing.rules.base import TradingRule
from orion.storage.models_gold import CandidateTrade
from orion.storage.models_silver import SilverSignal

logger = logging.getLogger(__name__)


class RuleEngine:
    """
    Orchestrates the execution of trading rules.
    """

    def __init__(self, config: dict[Any, Any] | None = None):
        from orion.processing.rules.flow_rules import BearishPutPressureRule, BullishSweepRule

        cfg = config or {}
        overrides = cfg.get("rule_overrides", {})

        # Extract rule-specific configs
        # Prioritize 'rule_overrides', fallback to root keys (legacy), ensure defaults

        def get_rule_cfg(rule_id: str) -> Dict[str, Any]:
            return overrides.get(rule_id) or cfg.get(rule_id, {})

        bull_cfg = get_rule_cfg("rule_bullish_sweep_v1")
        bear_cfg = get_rule_cfg("rule_bearish_put_pressure_v1")

        self.rules: List[TradingRule] = [
            BullishSweepRule(min_premium=bull_cfg.get("min_premium", 10000.0)),
            BearishPutPressureRule(min_premium=bear_cfg.get("min_premium", 10000.0)),
        ]
        logger.info(f"RuleEngine initialized with overrides: {list(cfg.keys())}")

    def process_signals(self, signals: List[SilverSignal]) -> List[CandidateTrade]:
        candidates = []
        for signal in signals:
            for rule in self.rules:
                try:
                    candidate = rule.evaluate(signal)
                    if candidate:
                        candidates.append(candidate)
                except Exception as e:
                    logger.error(f"Error evaluating rule {rule.rule_id} for {signal.ticker}: {e}")

        return candidates
