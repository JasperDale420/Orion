from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuleRegistry:
    """
    Minimal rule registry for PRD DSL validation (FR 5.1.1).
    This is code-backed for now; can be migrated to DB-backed registry later.
    """

    _KNOWN_RULE_IDS: tuple[str, ...] = (
        # Rule-first hypotheses (PRD §9.1; implemented)
        "rule_bullish_sweep_v1",
        "rule_bearish_put_pressure_v1",
        # Baseline mean reversion (implemented)
        "rsi_oversold_v1",
        # Utilities / harness
        "smoke_test_rule",
        # Allowlist wildcard used by some legacy configs
        "*",
    )

    @classmethod
    def list_all(cls) -> list[str]:
        return list(cls._KNOWN_RULE_IDS)

    @classmethod
    def exists(cls, rule_id: str) -> bool:
        return rule_id in cls._KNOWN_RULE_IDS
