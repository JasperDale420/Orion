from datetime import UTC, datetime

import pytest

from orion.processing.feature_engine import FeatureEngine
from orion.storage.models import BronzeEvent


def create_mock_flow_event(ticker, premium, ts):
    return BronzeEvent(
        event_id=f"flow_{ts.isoformat()}",
        source="UW",
        event_type="UW_FLOW",
        event_ts_utc=ts,
        received_ts_utc=ts,
        payload={
            "ticker": ticker,
            "premium": premium,
            "aggressor_ind": "ASK",
            "spot_price": 100.0,
            "expiry": "2023-12-01",
            "strike_price": 105.0,
            "put_call": "C",
        },
    )


def create_mock_bar_event(ticker, ts):
    return BronzeEvent(
        event_id=f"bar_{ts.isoformat()}",
        source="ALPACA",
        event_type="ALPACA_BAR_1M",
        event_ts_utc=ts,
        received_ts_utc=ts,
        payload={"symbol": ticker, "c": 100.0, "o": 99.0, "h": 101.0, "l": 99.0, "v": 1000},
    )


def test_feature_engine_integrates_flow_simple():
    fe = FeatureEngine()
    now = datetime.now(UTC)

    # 1. Ingest Flow (60s ago)
    flow_event = create_mock_flow_event("AAPL", 50000.0, datetime.fromtimestamp(now.timestamp() - 60, UTC))
    fe.process_uw_flow([flow_event])

    # 2. Ingest Bar (trigger signal)
    bar_event = create_mock_bar_event("AAPL", now)
    signals = fe.process_alpaca_bars([bar_event])

    assert len(signals) == 1
    features = signals[0].features

    # Check if flow metrics are present
    assert "call_premium_15m" in features
    assert features["call_premium_15m"] == pytest.approx(50000.0)


def test_feature_engine_expires_old_flow_simple():
    fe = FeatureEngine()
    now = datetime.now(UTC)

    # Old flow (20 mins = 1200s ago)
    old_flow = create_mock_flow_event("AAPL", 100000.0, datetime.fromtimestamp(now.timestamp() - 1200, UTC))
    fe.process_uw_flow([old_flow])

    # Bar now
    bar_event = create_mock_bar_event("AAPL", now)
    signals = fe.process_alpaca_bars([bar_event])

    features = signals[0].features
    assert features.get("call_premium_15m", 0.0) == pytest.approx(0.0)
