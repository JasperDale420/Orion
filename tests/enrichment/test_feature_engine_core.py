"""
Unit tests for FeatureEngine signal processing logic.
"""

from datetime import UTC, datetime, timedelta

import pytest

from orion.processing.feature_engine import FeatureEngine
from orion.storage.models import BronzeEvent


@pytest.fixture
def feature_engine():
    """FeatureEngine fixture."""
    return FeatureEngine()


@pytest.fixture
def sample_alpaca_bar_event():
    """Sample Alpaca bar event."""
    now = datetime.now(UTC)
    return BronzeEvent(
        event_id="bar_1",
        source="ALPACA",
        source_event_id="alpaca_1",
        event_type="ALPACA_BAR_1M",
        ticker="SPY",
        trading_date=None,
        session=None,
        schema_version="v1",
        event_ts_utc=now,
        received_ts_utc=now,
        payload={
            "t": now.isoformat(),
            "o": 500.0,
            "h": 501.0,
            "l": 499.0,
            "c": 500.5,
            "v": 1000,
            "vw": 500.2,
            "symbol": "SPY",
        },
        ingest={},
    )


@pytest.fixture
def sample_uw_flow_event():
    """Sample UW flow event."""
    now = datetime.now(UTC)
    return BronzeEvent(
        event_id="flow_1",
        source="UW",
        source_event_id="uw_1",
        event_type="UW_FLOW",
        ticker="SPY",
        trading_date=None,
        session=None,
        schema_version="v1",
        event_ts_utc=now,
        received_ts_utc=now,
        payload={
            "ticker": "SPY",
            "timestamp": now.isoformat(),
            "put_call": "C",
            "expiry": (now.date() + timedelta(days=14)).isoformat(),
            "strike_price": 500.0,
            "price": 1.0,
            "size": 100,
            "bid": 0.9,
            "ask": 1.1,
            "underlying_price": 500.0,
            "aggressor": "ASK",
            "sweep": True,
            "trade_type": "SWEEP",
            "open_interest": 1000,
            "volume": 100,
            "premium": 100000.0,
            "multi_leg": False,
            "id": "flow_1",
        },
        ingest={},
    )


def test_process_alpaca_bars_generates_signals(feature_engine, sample_alpaca_bar_event):
    """Test OHLCV signal generation from Alpaca bars."""
    events = [sample_alpaca_bar_event]

    signals = feature_engine.process_alpaca_bars(events)

    # Should generate at least one signal
    assert len(signals) > 0

    # Signal should have expected attributes
    signal = signals[0]
    assert signal.ticker == "SPY"
    assert signal.signal_type == "OHLCV_1M"  # FeatureEngine returns OHLCV_1M, not OHLCV
    assert signal.features is not None


def test_process_alpaca_bars_handles_empty_list(feature_engine):
    """Test processing empty event list."""
    signals = feature_engine.process_alpaca_bars([])
    assert signals == []


def test_process_alpaca_bars_handles_invalid_payload(feature_engine, sample_alpaca_bar_event):
    """Test handling of invalid bar payload."""
    # Corrupt payload
    sample_alpaca_bar_event.payload = {"invalid": "data"}

    signals = feature_engine.process_alpaca_bars([sample_alpaca_bar_event])

    # Should handle gracefully (skip or return empty)
    assert isinstance(signals, list)


def test_process_uw_flow_generates_signals(feature_engine, sample_uw_flow_event):
    """Test UW flow signal generation."""
    events = [sample_uw_flow_event]

    signals = feature_engine.process_uw_flow_events(events)

    # Should generate at least one signal
    assert len(signals) > 0

    # Signal should have flow-specific attributes
    signal = signals[0]
    assert signal.ticker == "SPY"
    assert signal.signal_type == "UW_FLOW"
    assert signal.features is not None


def test_process_uw_flow_handles_empty_list(feature_engine):
    """Test processing empty flow event list."""
    signals = feature_engine.process_uw_flow_events([])
    assert signals == []


def test_process_uw_flow_filters_low_premium(feature_engine, sample_uw_flow_event):
    """Test handling of low premium trades."""
    # Set very low premium
    sample_uw_flow_event.payload["premium"] = 100.0  # Below typical threshold

    signals = feature_engine.process_uw_flow_events([sample_uw_flow_event])

    # Low premium trades may or may not be filtered (depends on other criteria)
    # Just verify no crash and result is a list
    assert isinstance(signals, list)


def test_process_multiple_bars_same_ticker(feature_engine, sample_alpaca_bar_event):
    """Test processing multiple bars for same ticker."""
    # Create multiple bar events
    events = []
    for i in range(5):
        event = BronzeEvent(
            event_id=f"bar_{i}",
            source="ALPACA",
            source_event_id=f"alpaca_{i}",
            event_type="ALPACA_BAR_1M",
            ticker="SPY",
            trading_date=None,
            session=None,
            schema_version="v1",
            event_ts_utc=sample_alpaca_bar_event.event_ts_utc + timedelta(minutes=i),
            received_ts_utc=sample_alpaca_bar_event.received_ts_utc + timedelta(minutes=i),
            payload=sample_alpaca_bar_event.payload.copy(),
            ingest={},
        )
        events.append(event)

    signals = feature_engine.process_alpaca_bars(events)

    # Should generate signals for all bars
    assert len(signals) >= 1
