import asyncio
import time

import pandas as pd
import pytest

from orion.processing.feature_engine import FeatureEngine
from orion.storage.models_gold import CandidateTrade


@pytest.mark.asyncio
async def test_compute_features_duplicate_index_does_not_crash():
    """Regression: a history frame with duplicate timestamps in its index must not
    raise 'Reindexing only valid with uniquely valued Index objects'.

    Overlapping Heber parquet partitions can produce two rows with the same
    bar timestamp but different values; full-row drop_duplicates does not
    collapse those, leaving a duplicated DatetimeIndex. _extract_price_features
    then called get_indexer on it and crashed, silently degrading enrichment.
    """
    engine = FeatureEngine()
    ticker = "DUP_TICKER"

    ts = pd.Timestamp("2025-01-01 10:02:00", tz="UTC")
    dates = pd.DatetimeIndex(
        [
            pd.Timestamp("2025-01-01 10:00:00", tz="UTC"),
            pd.Timestamp("2025-01-01 10:01:00", tz="UTC"),
            ts,  # duplicate timestamp, stale value
            ts,  # duplicate timestamp, fresh value
        ]
    )
    df = pd.DataFrame(
        {
            "close": [100.0, 101.0, 199.0, 102.0],
            "volume": [1000, 1100, 9999, 1200],
            "RSI_14": [50.0, 52.0, 11.0, 55.0],
        },
        index=dates,
    )
    engine.history[ticker] = df

    candidate = CandidateTrade(
        candidate_id="dup_1", ticker=ticker, timestamp_utc=ts, direction="LONG", rule_id="test_rule", evidence={}
    )

    features = await engine.compute(candidate)

    # Must not crash, and must return the freshest (keep="last") row for the dup ts.
    assert features["close"] == 102.0
    assert features["volume"] == 1200.0
    assert features["rsi_14"] == 55.0


@pytest.mark.asyncio
async def test_hydrate_dedupes_duplicate_bars_by_latest_available(monkeypatch):
    """Hydration must collapse two revisions of the SAME bar to one row, keeping
    the latest-available revision via the ts_available tie-breaker — NOT arbitrary
    read_bars row order — so a non-unique index can never reach get_indexer.

    Row order here is fresh-first / stale-second, so a naive keep="last" would
    wrongly pick the stale revision; the ts_available sort must still pick fresh.
    """
    import orion.processing.feature_engine as fe

    bar_ts = "2025-01-01 10:00:00+00:00"

    class _FakeReader:
        def read_bars(self, **_kwargs):
            return pd.DataFrame(
                {
                    "ticker": ["AAPL", "AAPL"],
                    "bar_start_ts": [bar_ts, bar_ts],
                    "open": [10.0, 10.0],
                    "high": [11.0, 11.0],
                    "low": [9.0, 9.0],
                    "close": [102.0, 99.0],  # row0 fresh (later avail), row1 stale
                    "volume": [700, 500],
                    "ts_available": ["2025-01-01 10:00:30+00:00", "2025-01-01 10:00:05+00:00"],
                }
            )

    monkeypatch.setattr(fe, "get_heber_reader", lambda: _FakeReader())

    engine = FeatureEngine()
    await engine._hydrate_single_ticker("AAPL")

    df = engine.history["AAPL"]
    assert len(df) == 1  # two revisions collapsed to one bar
    assert not df.index.has_duplicates
    assert float(df.iloc[0]["close"]) == 102.0  # latest-available revision won


@pytest.mark.asyncio
async def test_hydrate_single_ticker_yields_while_blocking_reader_runs(monkeypatch):
    """Hydration reads can be slow, but must not freeze the event loop."""
    import orion.processing.feature_engine as fe

    class _SlowReader:
        def read_bars(self, **_kwargs):
            time.sleep(0.05)
            return pd.DataFrame()

    monkeypatch.setattr(fe, "get_heber_reader", lambda: _SlowReader())

    engine = FeatureEngine()
    ticks = 0
    done = asyncio.Event()

    async def count_event_loop_ticks() -> None:
        nonlocal ticks
        while not done.is_set():
            ticks += 1
            await asyncio.sleep(0.005)

    ticker_task = asyncio.create_task(count_event_loop_ticks())
    await engine._hydrate_single_ticker("AAPL")
    done.set()
    await ticker_task

    assert ticks > 0


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


def test_process_alpaca_bars_cold_start_warning(caplog):
    """Test that a warning is logged when process_alpaca_bars is called without hydration."""
    import logging

    caplog.set_level(logging.WARNING)
    engine = FeatureEngine()

    # Process without calling hydrate_history - should trigger warning
    result = engine.process_alpaca_bars([])

    assert "hydrate_history" in caplog.text
    assert result == []  # Should still work, just with warning


@pytest.mark.asyncio
async def test_process_alpaca_bars_no_warning_after_hydration(caplog):
    """Test that no warning is logged after hydrate_history is called."""
    import logging

    caplog.set_level(logging.WARNING)
    engine = FeatureEngine()

    # Simulate successful hydration by directly setting the flag
    # This avoids needing to mock the full DB layer
    engine._hydrated = True

    # Clear the log before processing
    caplog.clear()

    # Process after hydration - should not trigger warning
    result = engine.process_alpaca_bars([])

    assert "hydrate_history" not in caplog.text
    assert result == []
