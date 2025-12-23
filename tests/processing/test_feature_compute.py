import pandas as pd
import pytest
from orion.processing.feature_engine import FeatureEngine
from orion.storage.models_gold import CandidateTrade


@pytest.mark.asyncio
async def test_compute_features_deterministic():
    engine = FeatureEngine()

    # Setup Data
    ticker = "TEST_TICKER"
    # Create 5 minutes of data
    dates = pd.date_range(start="2025-01-01 10:00:00", periods=5, freq="1min", tz="UTC")
    df = pd.DataFrame(
        {
            "close": [100.0, 101.0, 102.0, 101.5, 103.0],
            "volume": [1000, 1100, 1200, 1000, 1500],
            "RSI_14": [50.0, 52.0, 55.0, 54.0, 60.0],
        },
        index=dates,
    )

    engine.history[ticker] = df

    # Create Candidate at known time
    ts = dates[2]  # 10:02:00
    candidate = CandidateTrade(
        candidate_id="test_1", ticker=ticker, timestamp_utc=ts, direction="LONG", rule_id="test_rule", evidence={}
    )

    # Compute
    features = await engine.compute(candidate)

    assert features["close"] == 102.0
    assert features["volume"] == 1200.0
    assert features["rsi_14"] == 55.0

    # Test Missing
    candidate_missing = CandidateTrade(
        candidate_id="test_2", ticker="UNK", timestamp_utc=ts, direction="LONG", rule_id="test_rule", evidence={}
    )
    features_missing = await engine.compute(candidate_missing)
    assert not features_missing  # Should be empty
