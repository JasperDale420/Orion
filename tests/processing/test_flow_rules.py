"""Bucket entry rules (2026-07 overhaul).

Time handling in fixtures: the ET entry-window check reads
``signal.signal_ts_utc`` (pinned to a fixed ET wall-clock time today) while
the signal-age check prefers ``features.flow_ts_utc`` (pinned relative to the
real now) — so tests are deterministic no matter when they run.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from orion.processing.rules.flow_rules import (
    ShortSwingBucketRule,
    SwingBucketRule,
    ZeroDTEBucketRule,
)
from orion.storage.models_silver import SilverSignal

_ET = ZoneInfo("America/New_York")


def _et_today(hour: int, minute: int) -> datetime:
    now_et = datetime.now(_ET)
    return now_et.replace(hour=hour, minute=minute, second=0, microsecond=0).astimezone(UTC)


def _make_signal(
    ticker: str = "SPY",
    *,
    dte: int = 0,
    premium: float = 80_000.0,
    put_call: str = "CALL",
    aggressor: str = "ASK",
    is_sweep: bool = True,
    delta: float | None = 0.4,
    volume: float | None = 900.0,
    age_seconds: float = 10.0,
    signal_et: tuple[int, int] = (10, 30),
    **extra_features,
) -> SilverSignal:
    features = {
        "is_sweep": is_sweep,
        "put_call": put_call,
        "premium": premium,
        "aggressor_ind": aggressor,
        "dte": dte,
        "underlying_price": 500.0,
        "event_id": "evt_1",
        "flow_ts_utc": (datetime.now(UTC) - timedelta(seconds=age_seconds)).isoformat(),
    }
    if delta is not None:
        features["delta"] = delta
    if volume is not None:
        features["volume_contract"] = volume
    features.update(extra_features)
    return SilverSignal(
        signal_id="sig_123",
        ticker=ticker,
        signal_ts_utc=_et_today(*signal_et),
        signal_type="UW_FLOW",
        features=features,
    )


# --- Common core (via the 0DTE rule) ----------------------------------------


def test_zero_dte_call_sweep_matches():
    rule = ZeroDTEBucketRule()
    candidate = rule.evaluate(_make_signal())
    assert candidate is not None
    assert candidate.rule_id == "rule_0dte_sweep_v2"
    assert candidate.direction == "LONG"
    assert candidate.option_type == "CALL"
    assert candidate.confidence == 1.0
    assert candidate.source == "UW"
    assert (candidate.evidence or {}).get("delta_missing") is False
    assert (candidate.evidence or {}).get("signal_age_seconds") is not None
    assert (candidate.execution_params or {}).get("limit_price") == 500.0


def test_put_sweep_trades_with_the_flow():
    """Both directions: a put sweep is bought as a put, still direction LONG."""
    rule = ZeroDTEBucketRule()
    candidate = rule.evaluate(_make_signal(put_call="PUT"))
    assert candidate is not None
    assert candidate.direction == "LONG"
    assert candidate.option_type == "PUT"


def test_rejects_non_sweep():
    assert ZeroDTEBucketRule().evaluate(_make_signal(is_sweep=False)) is None


def test_rejects_bid_aggressor():
    assert ZeroDTEBucketRule().evaluate(_make_signal(aggressor="BID")) is None


def test_rejects_below_premium_floor():
    assert ZeroDTEBucketRule().evaluate(_make_signal(premium=30_000.0)) is None


def test_no_premium_ceiling():
    """The old $100-150k band is gone — a $5M sweep passes."""
    assert ZeroDTEBucketRule().evaluate(_make_signal(premium=5_000_000.0)) is not None


def test_rejects_delta_out_of_band():
    assert ZeroDTEBucketRule().evaluate(_make_signal(delta=0.05)) is None


def test_missing_delta_passes_and_is_flagged():
    candidate = ZeroDTEBucketRule().evaluate(_make_signal(delta=None))
    assert candidate is not None
    assert (candidate.evidence or {}).get("delta_missing") is True


def test_rejects_below_volume_floor():
    assert ZeroDTEBucketRule().evaluate(_make_signal(volume=50.0)) is None


def test_missing_volume_passes_and_is_flagged():
    candidate = ZeroDTEBucketRule().evaluate(_make_signal(volume=None))
    assert candidate is not None
    assert (candidate.evidence or {}).get("volume_missing") is True


def test_rejects_stale_signal():
    """0DTE age budget is 120s — a 5-minute-old print is dead."""
    assert ZeroDTEBucketRule().evaluate(_make_signal(age_seconds=300.0)) is None


def test_rejects_outside_entry_window():
    # Before 9:35 ET
    assert ZeroDTEBucketRule().evaluate(_make_signal(signal_et=(9, 20))) is None
    # After the 15:00 ET 0DTE cutoff
    assert ZeroDTEBucketRule().evaluate(_make_signal(signal_et=(15, 10))) is None


# --- Bucket envelopes --------------------------------------------------------


def test_zero_dte_rejects_single_name():
    """0DTE trades index ETFs only — single-name 0DTE is a spread lottery."""
    assert ZeroDTEBucketRule().evaluate(_make_signal(ticker="NVDA")) is None


def test_zero_dte_rejects_wrong_dte():
    assert ZeroDTEBucketRule().evaluate(_make_signal(dte=1)) is None


def test_short_swing_envelope():
    rule = ShortSwingBucketRule()
    ok = rule.evaluate(_make_signal(ticker="NVDA", dte=2, age_seconds=200.0))
    assert ok is not None
    assert ok.rule_id == "rule_short_swing_v2"
    assert rule.evaluate(_make_signal(ticker="NVDA", dte=0)) is None
    assert rule.evaluate(_make_signal(ticker="NVDA", dte=5)) is None
    # 300s budget: a 200s-old signal passes, a 400s-old one doesn't.
    assert rule.evaluate(_make_signal(ticker="NVDA", dte=2, age_seconds=400.0)) is None


def test_short_swing_rejects_off_universe_ticker():
    assert ShortSwingBucketRule().evaluate(_make_signal(ticker="ZZZZ", dte=2)) is None


def test_swing_envelope():
    rule = SwingBucketRule()
    ok = rule.evaluate(_make_signal(ticker="AAPL", dte=10, premium=150_000.0, age_seconds=600.0))
    assert ok is not None
    assert ok.rule_id == "rule_swing_v2"
    # Higher conviction bar: $80k is below the swing floor.
    assert rule.evaluate(_make_signal(ticker="AAPL", dte=10, premium=80_000.0)) is None
    assert rule.evaluate(_make_signal(ticker="AAPL", dte=20, premium=150_000.0)) is None


def test_non_flow_signal_ignored():
    signal = _make_signal()
    signal.signal_type = "OHLCV_1M"
    assert ZeroDTEBucketRule().evaluate(signal) is None


def test_single_letter_put_call_accepted():
    candidate = ZeroDTEBucketRule().evaluate(_make_signal(put_call="C"))
    assert candidate is not None
    assert candidate.option_type == "CALL"


@pytest.mark.parametrize("rule_cls", [ZeroDTEBucketRule, ShortSwingBucketRule, SwingBucketRule])
def test_empty_features_ignored(rule_cls):
    signal = _make_signal()
    signal.features = {}
    assert rule_cls().evaluate(signal) is None
