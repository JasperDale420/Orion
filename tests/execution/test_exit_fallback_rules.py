"""Tests for deterministic exit fallback rules."""

from datetime import UTC, datetime, timedelta

import pytest

from orion.execution.exit_fallback_rules import (
    DrawdownFromPeakRule,
    ProfitTargetRule,
    TimeToExpiryRule,
    evaluate_fallback_rules,
)


def _make_position(
    *,
    symbol: str = "QQQ_PUT",
    return_pct: float = 0.0,
    max_return_pct: float = 0.0,
    entry_time: datetime | None = None,
    dte_at_entry: int = 7,
    expiry_date: datetime | None = None,
):
    """Test fixture mimicking TrackedPosition fields the rules read."""
    from types import SimpleNamespace

    return SimpleNamespace(
        symbol=symbol,
        unrealized_pnl_pct=return_pct,
        max_return_pct=max_return_pct,
        entry_time=entry_time or (datetime.now(UTC) - timedelta(days=1)),
        dte_at_entry=dte_at_entry,
        option_symbol=symbol,
        expiry_date=expiry_date,
    )


# --- ProfitTargetRule -------------------------------------------------------


def test_profit_target_triggers_above_threshold():
    rule = ProfitTargetRule(target_pct=1.00)
    pos = _make_position(return_pct=1.05)
    sig = rule.should_exit(pos)
    assert sig is not None
    assert sig.urgency == "SOON"
    assert "profit_target" in sig.rule_id


def test_profit_target_does_not_trigger_below_threshold():
    rule = ProfitTargetRule(target_pct=1.00)
    pos = _make_position(return_pct=0.50)
    assert rule.should_exit(pos) is None


def test_profit_target_disabled_when_target_zero():
    rule = ProfitTargetRule(target_pct=0)
    pos = _make_position(return_pct=5.0)
    assert rule.should_exit(pos) is None


# --- TimeToExpiryRule -------------------------------------------------------


def test_time_to_expiry_triggers_when_dte_below_min():
    rule = TimeToExpiryRule(min_dte=1)
    pos = _make_position(
        expiry_date=datetime.now(UTC) + timedelta(hours=20),  # ~0 DTE
    )
    sig = rule.should_exit(pos)
    assert sig is not None
    assert sig.urgency == "IMMEDIATE"


def test_time_to_expiry_does_not_trigger_when_dte_above_min():
    rule = TimeToExpiryRule(min_dte=1)
    pos = _make_position(
        expiry_date=datetime.now(UTC) + timedelta(days=5),
    )
    assert rule.should_exit(pos) is None


def test_time_to_expiry_disabled_when_min_zero():
    rule = TimeToExpiryRule(min_dte=0)
    pos = _make_position(
        expiry_date=datetime.now(UTC) + timedelta(hours=1),
    )
    assert rule.should_exit(pos) is None


def test_time_to_expiry_handles_missing_expiry_gracefully():
    rule = TimeToExpiryRule(min_dte=1)
    pos = _make_position(expiry_date=None)
    assert rule.should_exit(pos) is None  # can't evaluate, don't fire


# --- DrawdownFromPeakRule ---------------------------------------------------


def test_drawdown_from_peak_triggers_on_retracement():
    rule = DrawdownFromPeakRule(max_drawdown_pct=0.50)
    # Peak was +200%, current is +50%. Retracement = (200 - 50) / 200 = 0.75.
    pos = _make_position(return_pct=0.50, max_return_pct=2.00)
    sig = rule.should_exit(pos)
    assert sig is not None
    assert sig.urgency == "SOON"


def test_drawdown_from_peak_does_not_trigger_on_small_retracement():
    rule = DrawdownFromPeakRule(max_drawdown_pct=0.50)
    # Peak +100%, current +80%. Retracement = 0.20 → below 0.50 threshold.
    pos = _make_position(return_pct=0.80, max_return_pct=1.00)
    assert rule.should_exit(pos) is None


def test_drawdown_from_peak_requires_peak_above_breakeven():
    rule = DrawdownFromPeakRule(max_drawdown_pct=0.50)
    # Never been profitable → no peak to protect.
    pos = _make_position(return_pct=-0.20, max_return_pct=-0.10)
    assert rule.should_exit(pos) is None


def test_drawdown_from_peak_disabled_when_threshold_zero():
    rule = DrawdownFromPeakRule(max_drawdown_pct=0)
    pos = _make_position(return_pct=0.10, max_return_pct=2.00)
    assert rule.should_exit(pos) is None


# --- Composition -----------------------------------------------------------


def test_evaluate_fallback_rules_returns_first_match():
    """Profit and DTE both trigger; profit fires first (more specific)."""
    pos = _make_position(
        return_pct=1.50,
        max_return_pct=1.50,
        expiry_date=datetime.now(UTC) + timedelta(hours=12),
    )
    sig = evaluate_fallback_rules(
        pos,
        profit_target_pct=1.00,
        min_dte=1,
        max_drawdown_pct=0.50,
    )
    assert sig is not None
    assert sig.rule_id == "profit_target_v1"


def test_evaluate_fallback_rules_returns_none_when_no_match():
    pos = _make_position(
        return_pct=0.20,
        max_return_pct=0.30,
        expiry_date=datetime.now(UTC) + timedelta(days=10),
    )
    sig = evaluate_fallback_rules(
        pos,
        profit_target_pct=1.00,
        min_dte=1,
        max_drawdown_pct=0.50,
    )
    assert sig is None
