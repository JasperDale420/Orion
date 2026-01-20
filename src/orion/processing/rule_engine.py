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
        from orion.processing.rules.flow_rules import (
            ShortSwingEntryRule,
            SwingEntryRule,
            ZeroDTESweepRule,
        )

        cfg = config or {}
        overrides = cfg.get("rule_overrides", {})

        def get_rule_cfg(rule_id: str) -> Dict[str, Any]:
            return overrides.get(rule_id) or cfg.get(rule_id, {})

        zero_dte_cfg = get_rule_cfg("rule_0dte_sweep_v1")
        swing_cfg = get_rule_cfg("rule_swing_entry_v1")
        short_swing_cfg = get_rule_cfg("rule_short_swing_entry_v1")

        # Data-driven entry rules based on price target analysis
        self.rules: List[TradingRule] = [
            ZeroDTESweepRule(
                min_premium=zero_dte_cfg.get("min_premium", 100000.0),
                max_premium=zero_dte_cfg.get("max_premium", 150000.0),
            ),
            SwingEntryRule(
                min_premium=swing_cfg.get("min_premium", 50000.0),
                max_premium=swing_cfg.get("max_premium", 75000.0),
            ),
            ShortSwingEntryRule(
                min_premium=short_swing_cfg.get("min_premium", 75000.0),
                max_premium=short_swing_cfg.get("max_premium", 100000.0),
            ),
        ]
        logger.info(f"RuleEngine initialized with {len(self.rules)} data-driven rules")

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
