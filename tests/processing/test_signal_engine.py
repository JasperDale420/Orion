from datetime import datetime

from orion.processing.signal_engine import SignalEngine
from orion.storage.models_silver import SilverSignal


def test_signal_engine_bullish_sweep():
    engine = SignalEngine()

    # Mock Silver Signal
    sig = SilverSignal(
        signal_id="sig1",
        ticker="TSLA",
        signal_ts_utc=datetime.utcnow(),
        signal_type="FEATURE_EVENT",
        features={"premium_usd": 60000, "call_put": "C", "is_sweep": True},
    )

    candidates = engine.process_signals([sig])

    assert len(candidates) == 1
    c = candidates[0]
    assert c.ticker == "TSLA"
    assert c.direction == "LONG"
    assert c.rule_id == "bullish_sweep_v1"
    assert c.evidence["premium"] == 60000


def test_signal_engine_no_match():
    engine = SignalEngine()

    # Weak signal
    sig = SilverSignal(
        signal_id="sig2",
        ticker="TSLA",
        signal_ts_utc=datetime.utcnow(),
        signal_type="FEATURE_EVENT",
        features={
            "premium_usd": 1000,  # Too low
            "call_put": "C",
            "is_sweep": True,
        },
    )

    candidates = engine.process_signals([sig])
    assert len(candidates) == 0
