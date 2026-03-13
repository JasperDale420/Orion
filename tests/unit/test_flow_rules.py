from datetime import UTC, datetime

import pytest

from orion.processing.rules.flow_rules import BearishPutPressureRule, BullishSweepRule
from orion.storage.models_silver import SilverSignal


@pytest.fixture
def mock_signal():
    return SilverSignal(
        signal_id="sig_123", ticker="SPY", signal_ts_utc=datetime.now(UTC), signal_type="UW_FLOW", features={}
    )


def test_bullish_sweep_match(mock_signal):
    rule = BullishSweepRule(min_premium=5000.0)
    mock_signal.features = {
        "is_sweep": True,
        "put_call": "CALL",
        "premium": 6000.0,
        "aggressor_ind": "ASK",
        "dte": 15,
        "delta": 0.5,
        "underlying_price": 400.0,
        "event_id": "evt_1",
    }

    candidate = rule.evaluate(mock_signal)
    assert candidate is not None
    assert candidate.direction == "LONG"
    assert candidate.rule_id == rule.rule_id
    assert candidate.source == "UW"
    assert (candidate.execution_params or {}).get("limit_price") == 400.0
    assert (candidate.evidence or {}).get("event_ids") == ["evt_1"]
    assert isinstance((candidate.evidence or {}).get("rollup_ids"), list)
    assert (candidate.evidence or {}).get("segments") == ["UW_FLOW"]


def test_bullish_sweep_no_match_premium(mock_signal):
    rule = BullishSweepRule(min_premium=10000.0)
    mock_signal.features = {
        "is_sweep": True,
        "put_call": "CALL",
        "premium": 5000.0,  # Too low
        "aggressor_ind": "ASK",
        "dte": 15,
    }
    assert rule.evaluate(mock_signal) is None


def test_bearish_put_pressure_match(mock_signal):
    rule = BearishPutPressureRule(min_premium=5000.0)
    mock_signal.features = {
        "put_call": "PUT",
        "premium": 8000.0,
        "aggressor_ind": "ASK",
        "dte": 5,
        "underlying_price": 400.0,
        "event_id": "evt_2",
    }

    candidate = rule.evaluate(mock_signal)
    assert candidate is not None
    assert candidate.direction == "SHORT"
    assert candidate.rule_id == rule.rule_id
    assert candidate.source == "UW"
    assert (candidate.execution_params or {}).get("limit_price") == 400.0
    assert (candidate.evidence or {}).get("event_ids") == ["evt_2"]
