"""Deterministic exit fallback rules.

These rules run independent of the ML exit classifier. They exist because
the classifier was observed returning a constant 0.17 confidence on
2026-05-19 regardless of position return — leaving profitable positions
to decay through expiry. See FOLLOWUPS.md item #0.

Each rule returns an ExitSignal or None. `evaluate_fallback_rules` is the
composition entry point used by position_monitor.evaluate_exits — it
returns the first rule that fires, or None.

The rules are intentionally conservative defaults — they're a safety net,
not the primary exit strategy. The ML classifier (once fixed) remains the
preferred signal source; these only fire when the classifier hasn't.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass
class ExitSignal:
    """Compatible shape with ExitPrediction used by execute_exits."""

    rule_id: str
    reason: str
    urgency: str  # IMMEDIATE, SOON, CONSIDER
    confidence: float = 1.0
    should_exit: bool = True


class ProfitTargetRule:
    """Exit when position return crosses the target threshold."""

    rule_id = "profit_target_v1"

    def __init__(self, target_pct: float) -> None:
        self.target_pct = target_pct

    def should_exit(self, position: Any) -> ExitSignal | None:
        if self.target_pct <= 0:
            return None
        ret = float(getattr(position, "unrealized_pnl_pct", 0.0) or 0.0)
        if ret < self.target_pct:
            return None
        return ExitSignal(
            rule_id=self.rule_id,
            reason=f"profit target hit: return={ret:.1%} >= target={self.target_pct:.1%}",
            urgency="SOON",
        )


class TimeToExpiryRule:
    """Exit when remaining time-to-expiry is below min_dte days."""

    rule_id = "time_to_expiry_v1"

    def __init__(self, min_dte: int) -> None:
        self.min_dte = min_dte

    def should_exit(self, position: Any) -> ExitSignal | None:
        if self.min_dte <= 0:
            return None
        expiry = getattr(position, "expiry_date", None)
        if expiry is None:
            # Can't evaluate without expiry; rule doesn't fire (no false trigger).
            return None
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        remaining = (expiry - datetime.now(UTC)).total_seconds() / 86400.0
        if remaining > self.min_dte:
            return None
        return ExitSignal(
            rule_id=self.rule_id,
            reason=f"time to expiry: {remaining:.2f} days <= min_dte={self.min_dte}",
            urgency="IMMEDIATE",
        )


class DrawdownFromPeakRule:
    """Exit when position has retraced max_drawdown_pct from its peak return.

    Only fires when peak_return > 0 (don't protect a loss-trajectory peak).
    """

    rule_id = "drawdown_from_peak_v1"

    def __init__(self, max_drawdown_pct: float) -> None:
        self.max_drawdown_pct = max_drawdown_pct

    def should_exit(self, position: Any) -> ExitSignal | None:
        if self.max_drawdown_pct <= 0:
            return None
        peak = float(getattr(position, "max_return_pct", 0.0) or 0.0)
        if peak <= 0:
            return None  # never been profitable; no peak to defend
        current = float(getattr(position, "unrealized_pnl_pct", 0.0) or 0.0)
        # Retracement as a fraction of peak: (peak - current) / peak.
        # peak=200% current=50% → (2.0 - 0.5)/2.0 = 0.75 retracement.
        retracement = (peak - current) / peak
        if retracement < self.max_drawdown_pct:
            return None
        return ExitSignal(
            rule_id=self.rule_id,
            reason=(
                f"drawdown from peak: retracement={retracement:.1%} >= "
                f"max={self.max_drawdown_pct:.1%} (peak={peak:.1%}, current={current:.1%})"
            ),
            urgency="SOON",
        )


def evaluate_fallback_rules(
    position: Any,
    *,
    profit_target_pct: float,
    min_dte: int,
    max_drawdown_pct: float,
) -> ExitSignal | None:
    """Run all fallback rules in priority order. Return first signal that fires.

    Priority is: profit → time-to-expiry → drawdown. Profit first because
    "exit at +100%" is the most informative signal; expiry next because
    it's a hard deadline; drawdown last as a safety net.
    """
    for rule in (
        ProfitTargetRule(profit_target_pct),
        TimeToExpiryRule(min_dte),
        DrawdownFromPeakRule(max_drawdown_pct),
    ):
        signal = rule.should_exit(position)
        if signal is not None:
            return signal
    return None
