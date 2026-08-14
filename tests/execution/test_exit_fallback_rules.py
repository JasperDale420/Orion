"""Tests for deterministic exit fallback rules."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from orion.execution.exit_fallback_rules import (
    DEFAULT_BUCKET_PARAMS,
    BucketExitParams,
    DisasterValveRule,
    DrawdownFromPeakRule,
    MaxHoldRule,
    NoProgressRule,
    ProfitTargetRule,
    StopLossRule,
    TimeToExpiryRule,
    ZeroDTEFlattenRule,
    evaluate_fallback_rules,
    resolve_exit_params,
)


def _make_position(
    *,
    symbol: str = "QQQ_PUT",
    return_pct: float = 0.0,  # fraction: 1.05 = +105%
    max_return_pct: float = 0.0,  # fraction: 2.00 = +200% peak
    entry_time: datetime | None = None,
    dte_at_entry: int = 7,
    expiry_date: datetime | None = None,
    bucket: str = "SWING",
):
    """Test fixture mimicking TrackedPosition fields the rules read."""
    from types import SimpleNamespace

    # TrackedPosition stores these in PERCENT (see position_monitor.py:141)
    # — fixture converts so test bodies stay in the easier-to-read fraction form.
    return SimpleNamespace(
        symbol=symbol,
        unrealized_pnl_pct=return_pct * 100.0,
        max_return_pct=max_return_pct * 100.0,
        entry_time=entry_time or (datetime.now(UTC) - timedelta(days=1)),
        dte_at_entry=dte_at_entry,
        option_symbol=symbol,
        expiry_date=expiry_date,
        bucket=bucket,
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


# --- StopLossRule -----------------------------------------------------------


def test_stop_loss_triggers_below_threshold():
    rule = StopLossRule(stop_loss_pct=0.35)
    pos = _make_position(return_pct=-0.40)
    sig = rule.should_exit(pos)
    assert sig is not None
    assert sig.urgency == "IMMEDIATE"
    assert sig.rule_id == "stop_loss_v1"


def test_stop_loss_does_not_trigger_above_threshold():
    rule = StopLossRule(stop_loss_pct=0.35)
    pos = _make_position(return_pct=-0.20)
    assert rule.should_exit(pos) is None


def test_stop_loss_disabled_when_zero():
    rule = StopLossRule(stop_loss_pct=0)
    pos = _make_position(return_pct=-0.99)
    assert rule.should_exit(pos) is None


# --- DisasterValveRule ------------------------------------------------------


def test_disaster_valve_triggers_on_catastrophic_loss():
    rule = DisasterValveRule(disaster_pct=0.60)
    pos = _make_position(return_pct=-0.75)
    sig = rule.should_exit(pos)
    assert sig is not None
    assert sig.urgency == "IMMEDIATE"
    assert sig.rule_id == "disaster_valve_v1"


def test_disaster_valve_does_not_trigger_above_threshold():
    rule = DisasterValveRule(disaster_pct=0.60)
    pos = _make_position(return_pct=-0.50)
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


# --- ZeroDTEFlattenRule -----------------------------------------------------


def _at_et(hour: int, minute: int) -> datetime:
    """A fixed 'now' at the given ET wall-clock time (as UTC for datetime.now)."""
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    return datetime(2026, 7, 1, hour, minute, tzinfo=et).astimezone(UTC)


def test_zero_dte_flatten_fires_after_cutoff():
    rule = ZeroDTEFlattenRule(flatten_after_et="15:45")
    pos = _make_position(bucket="0DTE")
    with patch("orion.execution.exit_fallback_rules.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: _at_et(15, 50).astimezone(tz or UTC)
        sig = rule.should_exit(pos)
    assert sig is not None
    assert sig.urgency == "IMMEDIATE"
    assert sig.rule_id == "zero_dte_flatten_v1"


def test_zero_dte_flatten_quiet_before_cutoff():
    rule = ZeroDTEFlattenRule(flatten_after_et="15:45")
    pos = _make_position(bucket="0DTE")
    with patch("orion.execution.exit_fallback_rules.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: _at_et(14, 0).astimezone(tz or UTC)
        assert rule.should_exit(pos) is None


def test_zero_dte_flatten_ignores_other_buckets():
    rule = ZeroDTEFlattenRule(flatten_after_et="15:45")
    pos = _make_position(bucket="SWING")
    with patch("orion.execution.exit_fallback_rules.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: _at_et(15, 50).astimezone(tz or UTC)
        assert rule.should_exit(pos) is None


def test_zero_dte_flatten_disabled_when_unset():
    rule = ZeroDTEFlattenRule(flatten_after_et=None)
    pos = _make_position(bucket="0DTE")
    assert rule.should_exit(pos) is None


def test_zero_dte_flatten_fires_for_mislabeled_bucket_expiring_today():
    """Defense in depth: a position mislabeled SWING (missing entry context)
    whose contract actually expires today must still hard-flatten."""
    rule = ZeroDTEFlattenRule(flatten_after_et="15:45")
    pos = _make_position(bucket="SWING", expiry_date=_at_et(16, 0))
    with patch("orion.execution.exit_fallback_rules.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: _at_et(15, 50).astimezone(tz or UTC)
        sig = rule.should_exit(pos)
    assert sig is not None
    assert sig.rule_id == "zero_dte_flatten_v1"


def test_zero_dte_flatten_quiet_for_future_expiry_other_bucket():
    rule = ZeroDTEFlattenRule(flatten_after_et="15:45")
    pos = _make_position(bucket="SWING", expiry_date=_at_et(16, 0) + timedelta(days=7))
    with patch("orion.execution.exit_fallback_rules.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: _at_et(15, 50).astimezone(tz or UTC)
        assert rule.should_exit(pos) is None


def test_zero_dte_flatten_not_a_day_early_for_midnight_utc_expiry():
    """Expiry is stored midnight UTC and encodes the CALENDAR date: a July-2
    contract (2026-07-02 00:00 UTC) seen at 15:50 ET on July 1 converts to
    July 1 20:00 ET — an ET-date comparison would flatten it a day early."""
    rule = ZeroDTEFlattenRule(flatten_after_et="15:45")
    # Tomorrow's contract, stored as midnight UTC of its calendar date.
    tomorrow_midnight_utc = (
        (_at_et(15, 50) + timedelta(days=1)).astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    )
    pos = _make_position(bucket="SHORT_SWING", expiry_date=tomorrow_midnight_utc)
    with patch("orion.execution.exit_fallback_rules.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: _at_et(15, 50).astimezone(tz or UTC)
        assert rule.should_exit(pos) is None


# --- ZeroDTEFlattenRule: early-close sessions -------------------------------


def _close_et(hour: int, minute: int) -> datetime:
    """A session close at the given ET wall-clock time on the fixture's date."""
    from zoneinfo import ZoneInfo

    return datetime(2026, 7, 1, hour, minute, tzinfo=ZoneInfo("America/New_York"))


def test_zero_dte_flatten_fires_before_early_close():
    """REGRESSION: a 13:00 ET early close never reaches 15:45, so the fixed
    cutoff never fired and an ITM 0DTE was auto-exercised at expiry."""
    rule = ZeroDTEFlattenRule(flatten_after_et="15:45", session_close=_close_et(13, 0))
    pos = _make_position(bucket="0DTE")
    with patch("orion.execution.exit_fallback_rules.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: _at_et(12, 46).astimezone(tz or UTC)
        sig = rule.should_exit(pos)
    assert sig is not None
    assert sig.urgency == "IMMEDIATE"
    assert sig.rule_id == "zero_dte_flatten_v1"


def test_zero_dte_flatten_quiet_before_early_close_cutoff():
    """The early-close cutoff is 12:45 (13:00 minus the fill buffer) — not earlier."""
    rule = ZeroDTEFlattenRule(flatten_after_et="15:45", session_close=_close_et(13, 0))
    pos = _make_position(bucket="0DTE")
    with patch("orion.execution.exit_fallback_rules.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: _at_et(12, 30).astimezone(tz or UTC)
        assert rule.should_exit(pos) is None


def test_zero_dte_flatten_early_close_fires_for_mislabeled_bucket():
    """Defence in depth still applies on an early close: a today-expiring
    contract mislabeled SWING must flatten before the 13:00 close too."""
    rule = ZeroDTEFlattenRule(flatten_after_et="15:45", session_close=_close_et(13, 0))
    pos = _make_position(bucket="SWING", expiry_date=_at_et(16, 0))
    with patch("orion.execution.exit_fallback_rules.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: _at_et(12, 50).astimezone(tz or UTC)
        sig = rule.should_exit(pos)
    assert sig is not None
    assert sig.rule_id == "zero_dte_flatten_v1"


def test_zero_dte_flatten_normal_close_keeps_configured_cutoff():
    """A 16:00 close leaves the configured 15:45 in force — unchanged behaviour."""
    rule = ZeroDTEFlattenRule(flatten_after_et="15:45", session_close=_close_et(16, 0))
    pos = _make_position(bucket="0DTE")
    with patch("orion.execution.exit_fallback_rules.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: _at_et(15, 44).astimezone(tz or UTC)
        assert rule.should_exit(pos) is None
        mock_dt.now.side_effect = lambda tz=None: _at_et(15, 46).astimezone(tz or UTC)
        assert rule.should_exit(pos) is not None


def test_zero_dte_flatten_without_session_close_uses_configured_cutoff():
    """No session in progress (pre-open / after-hours): fixed cutoff applies."""
    rule = ZeroDTEFlattenRule(flatten_after_et="15:45", session_close=None)
    pos = _make_position(bucket="0DTE")
    with patch("orion.execution.exit_fallback_rules.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: _at_et(15, 50).astimezone(tz or UTC)
        assert rule.should_exit(pos) is not None


def test_zero_dte_flatten_accepts_naive_session_close_as_utc():
    """A naive session close is read as UTC — same convention as expiry_date."""
    naive_close_utc = _close_et(13, 0).astimezone(UTC).replace(tzinfo=None)
    rule = ZeroDTEFlattenRule(flatten_after_et="15:45", session_close=naive_close_utc)
    pos = _make_position(bucket="0DTE")
    with patch("orion.execution.exit_fallback_rules.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: _at_et(12, 46).astimezone(tz or UTC)
        assert rule.should_exit(pos) is not None


def test_evaluate_fallback_rules_threads_session_close_to_flatten_rule():
    """Composition must pass the session close through, or the fix is inert."""
    pos = _make_position(bucket="0DTE", return_pct=0.10)
    with patch("orion.execution.exit_fallback_rules.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: _at_et(12, 50).astimezone(tz or UTC)
        sig = evaluate_fallback_rules(
            pos,
            params=resolve_exit_params("0DTE"),
            session_close=_close_et(13, 0),
        )
    assert sig is not None
    assert sig.rule_id == "zero_dte_flatten_v1"


# --- NoProgressRule ---------------------------------------------------------


def test_no_progress_fires_on_stuck_position():
    rule = NoProgressRule(no_progress_minutes=90, band_pct=0.15)
    pos = _make_position(
        return_pct=0.05,
        entry_time=datetime.now(UTC) - timedelta(minutes=120),
    )
    sig = rule.should_exit(pos)
    assert sig is not None
    assert sig.rule_id == "no_progress_v1"


def test_no_progress_quiet_when_position_moved():
    rule = NoProgressRule(no_progress_minutes=90, band_pct=0.15)
    pos = _make_position(
        return_pct=0.25,  # outside the ±15% band — thesis is working
        entry_time=datetime.now(UTC) - timedelta(minutes=120),
    )
    assert rule.should_exit(pos) is None


def test_no_progress_quiet_before_window():
    rule = NoProgressRule(no_progress_minutes=90, band_pct=0.15)
    pos = _make_position(
        return_pct=0.0,
        entry_time=datetime.now(UTC) - timedelta(minutes=30),
    )
    assert rule.should_exit(pos) is None


def test_no_progress_disabled_when_zero():
    rule = NoProgressRule(no_progress_minutes=0, band_pct=0.15)
    pos = _make_position(
        return_pct=0.0,
        entry_time=datetime.now(UTC) - timedelta(days=3),
    )
    assert rule.should_exit(pos) is None


# --- MaxHoldRule ------------------------------------------------------------


def test_max_hold_fires_past_window():
    rule = MaxHoldRule(max_hold_hours=54)
    pos = _make_position(entry_time=datetime.now(UTC) - timedelta(hours=60))
    sig = rule.should_exit(pos)
    assert sig is not None
    assert sig.rule_id == "max_hold_v1"
    assert sig.urgency == "SOON"


def test_max_hold_quiet_inside_window():
    rule = MaxHoldRule(max_hold_hours=54)
    pos = _make_position(entry_time=datetime.now(UTC) - timedelta(hours=10))
    assert rule.should_exit(pos) is None


def test_max_hold_disabled_when_zero():
    rule = MaxHoldRule(max_hold_hours=0)
    pos = _make_position(entry_time=datetime.now(UTC) - timedelta(days=30))
    assert rule.should_exit(pos) is None


def test_max_hold_handles_naive_entry_time():
    rule = MaxHoldRule(max_hold_hours=54)
    naive = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=60)
    pos = _make_position(entry_time=naive)
    assert rule.should_exit(pos) is not None


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


def test_drawdown_not_armed_until_min_peak():
    rule = DrawdownFromPeakRule(max_drawdown_pct=0.40, min_peak_pct=0.25)
    # Peak only +10% — trailing stop not armed even though retracement is 100%.
    pos = _make_position(return_pct=0.0, max_return_pct=0.10)
    assert rule.should_exit(pos) is None


def test_drawdown_armed_once_min_peak_reached():
    rule = DrawdownFromPeakRule(max_drawdown_pct=0.40, min_peak_pct=0.25)
    # Peak +30% (armed), current +5% → retracement 0.83 ≥ 0.40.
    pos = _make_position(return_pct=0.05, max_return_pct=0.30)
    assert rule.should_exit(pos) is not None


# --- resolve_exit_params ----------------------------------------------------


def test_resolve_exit_params_defaults():
    params = resolve_exit_params("SHORT_SWING")
    assert params == DEFAULT_BUCKET_PARAMS["SHORT_SWING"]


def test_resolve_exit_params_unknown_bucket_gets_swing():
    assert resolve_exit_params("???") == DEFAULT_BUCKET_PARAMS["SWING"]


def test_resolve_exit_params_applies_overrides():
    params = resolve_exit_params("0DTE", {"0DTE": {"profit_target_pct": 0.55}})
    assert params.profit_target_pct == 0.55
    # untouched fields keep bucket defaults
    assert params.stop_loss_pct == DEFAULT_BUCKET_PARAMS["0DTE"].stop_loss_pct


def test_resolve_exit_params_ignores_other_bucket_overrides():
    params = resolve_exit_params("SWING", {"0DTE": {"profit_target_pct": 0.55}})
    assert params == DEFAULT_BUCKET_PARAMS["SWING"]


# --- Composition -----------------------------------------------------------

_TEST_PARAMS = BucketExitParams(
    profit_target_pct=1.00,
    stop_loss_pct=0.35,
    min_dte=1,
    max_hold_hours=54,
    max_drawdown_pct=0.50,
)


def test_evaluate_fallback_rules_returns_first_match():
    """Profit and DTE both trigger; profit fires first (more specific)."""
    pos = _make_position(
        return_pct=1.50,
        max_return_pct=1.50,
        expiry_date=datetime.now(UTC) + timedelta(hours=12),
        entry_time=datetime.now(UTC) - timedelta(hours=2),
    )
    sig = evaluate_fallback_rules(pos, params=_TEST_PARAMS)
    assert sig is not None
    assert sig.rule_id == "profit_target_v1"


def test_evaluate_fallback_rules_returns_none_when_no_match():
    pos = _make_position(
        return_pct=0.20,
        max_return_pct=0.30,
        expiry_date=datetime.now(UTC) + timedelta(days=10),
        entry_time=datetime.now(UTC) - timedelta(hours=2),
    )
    sig = evaluate_fallback_rules(pos, params=_TEST_PARAMS)
    assert sig is None


def test_stop_loss_beats_max_hold_when_both_fire():
    """Stop-loss outranks time stops — the risk limit is the reason to exit."""
    pos = _make_position(
        return_pct=-0.40,
        expiry_date=datetime.now(UTC) + timedelta(days=10),
        entry_time=datetime.now(UTC) - timedelta(hours=100),
    )
    sig = evaluate_fallback_rules(pos, params=_TEST_PARAMS)
    assert sig is not None
    assert sig.rule_id == "stop_loss_v1"


def test_disaster_valve_beats_stop_loss():
    pos = _make_position(
        return_pct=-0.80,
        entry_time=datetime.now(UTC) - timedelta(hours=2),
    )
    sig = evaluate_fallback_rules(pos, params=_TEST_PARAMS)
    assert sig is not None
    assert sig.rule_id == "disaster_valve_v1"


def test_dte_beats_drawdown_when_both_fire():
    """DTE has priority over drawdown — both trigger here; DTE wins."""
    pos = _make_position(
        return_pct=0.50,  # +50%, not enough for profit target
        max_return_pct=2.00,  # +200% peak → retracement = 0.75
        expiry_date=datetime.now(UTC) + timedelta(hours=12),  # ~0 DTE
        entry_time=datetime.now(UTC) - timedelta(hours=2),
    )
    sig = evaluate_fallback_rules(pos, params=_TEST_PARAMS)
    assert sig is not None
    assert sig.rule_id == "time_to_expiry_v1"


def test_only_drawdown_fires():
    """Profit target and DTE don't trigger; only drawdown does."""
    pos = _make_position(
        return_pct=0.50,  # +50%, below 100% target
        max_return_pct=2.00,  # +200% peak → retracement = 0.75
        expiry_date=datetime.now(UTC) + timedelta(days=10),  # plenty of time
        entry_time=datetime.now(UTC) - timedelta(hours=2),
    )
    sig = evaluate_fallback_rules(pos, params=_TEST_PARAMS)
    assert sig is not None
    assert sig.rule_id == "drawdown_from_peak_v1"


def test_max_hold_fires_via_composition():
    pos = _make_position(
        return_pct=0.10,
        expiry_date=datetime.now(UTC) + timedelta(days=10),
        entry_time=datetime.now(UTC) - timedelta(hours=100),
    )
    sig = evaluate_fallback_rules(pos, params=_TEST_PARAMS)
    assert sig is not None
    assert sig.rule_id == "max_hold_v1"


def test_default_swing_loser_now_exits():
    """The regression that mattered: a plain -45% SWING loser must exit.

    Under the old rules (profit +100% / DTE<=1 / drawdown-only-if-peaked)
    this position was never exited and bled to zero via theta.
    """
    pos = _make_position(
        return_pct=-0.45,
        max_return_pct=0.0,
        expiry_date=datetime.now(UTC) + timedelta(days=8),
        entry_time=datetime.now(UTC) - timedelta(hours=6),
        bucket="SWING",
    )
    sig = evaluate_fallback_rules(pos, params=resolve_exit_params("SWING"))
    assert sig is not None
    assert sig.rule_id == "stop_loss_v1"
