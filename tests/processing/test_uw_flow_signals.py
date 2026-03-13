from datetime import UTC, datetime, timedelta

from orion.processing.feature_engine import FeatureEngine
from orion.storage.models import BronzeEvent


def test_process_uw_flow_events_adds_evidence_and_dte_and_unique_signal_ids():
    fe = FeatureEngine()
    ts = datetime.now(UTC).replace(microsecond=0)

    ev1 = BronzeEvent(
        event_id="evt_1",
        source="UW",
        source_event_id="src_1",
        event_type="UW_FLOW",
        ticker="SPY",
        event_ts_utc=ts,
        received_ts_utc=ts,
        payload={
            "ticker": "SPY",
            "timestamp": ts.isoformat(),
            "expiry": (ts + timedelta(days=10)).date().isoformat(),
            "put_call": "C",
            "premium_usd": 12345.0,
            "aggressor": "ASK",
            "is_sweep": "true",
            "underlying_price": 500.0,
        },
    )

    ev2 = BronzeEvent(
        event_id="evt_2",
        source="UW",
        source_event_id="src_2",
        event_type="UW_FLOW",
        ticker="SPY",
        event_ts_utc=ts,
        received_ts_utc=ts,
        payload={**ev1.payload, "premium_usd": 23456.0},
    )

    signals = fe.process_uw_flow_events([ev1, ev2])
    assert len(signals) == 2
    assert signals[0].signal_type == "UW_FLOW"

    f0 = signals[0].features
    assert f0["event_id"] in {"evt_1", "evt_2"}
    assert f0["is_sweep"] is True
    assert f0["aggressor_ind"] == "ASK"
    assert f0["dte"] == 10

    # Same ticker+timestamp, different event_id => signal_id must be unique.
    assert signals[0].signal_id != signals[1].signal_id
