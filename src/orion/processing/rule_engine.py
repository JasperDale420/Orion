import logging
from typing import Any

from orion.processing.rules.base import TradingRule
from orion.storage.models_gold import CandidateTrade
from orion.storage.models_silver import SilverSignal

logger = logging.getLogger(__name__)


class RuleEngine:
    """
    Orchestrates the execution of trading rules.
    """

    def __init__(
        self,
        config: dict[Any, Any] | None = None,
        *,
        enforce_universe_and_window: bool | None = None,
    ):
        from orion.processing.rules.flow_rules import (
            ShortSwingBucketRule,
            SwingBucketRule,
            ZeroDTEBucketRule,
        )

        cfg = config or {}
        overrides = cfg.get("rule_overrides", {})

        def get_rule_cfg(rule_id: str) -> dict[str, Any]:
            return overrides.get(rule_id) or cfg.get(rule_id, {})

        zero_dte_cfg = get_rule_cfg("rule_0dte_sweep_v2")
        short_swing_cfg = get_rule_cfg("rule_short_swing_v2")
        swing_cfg = get_rule_cfg("rule_swing_v2")

        # Test harnesses inject synthetic tickers at arbitrary wall-clock
        # times — they pass enforce_universe_and_window=False explicitly to
        # bypass the allowlist/entry-window/clock-skew gates (live-market
        # concerns). Signal-quality checks always apply. Default (None)
        # derives from the stage so the plain-pytest path needs no wiring.
        if enforce_universe_and_window is None:
            from orion.config import system_settings

            enforce = system_settings.orion_stage != "test"
        else:
            enforce = enforce_universe_and_window

        # One rule per horizon bucket (2026-07 overhaul): premium floors,
        # both directions with the flow, liquid-universe allowlist, per-bucket
        # signal-age budgets. Loosen only the premium floor when a bucket runs
        # under its trades/day target — never sweep/aggressor/liquidity.
        self.rules: list[TradingRule] = [
            ZeroDTEBucketRule(
                min_premium=zero_dte_cfg.get("min_premium", 50_000.0),
                enforce_universe_and_window=enforce,
            ),
            ShortSwingBucketRule(
                min_premium=short_swing_cfg.get("min_premium", 50_000.0),
                enforce_universe_and_window=enforce,
            ),
            SwingBucketRule(
                min_premium=swing_cfg.get("min_premium", 100_000.0),
                enforce_universe_and_window=enforce,
            ),
        ]
        logger.info(f"RuleEngine initialized with {len(self.rules)} bucket rules")

    def process_signals(self, signals: list[SilverSignal]) -> list[CandidateTrade]:
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
