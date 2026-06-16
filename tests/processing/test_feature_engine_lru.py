"""LRU eviction tests for FeatureEngine per-ticker in-memory stores.

Guards audit finding #10: ``history`` and ``flow_history`` must not grow for
the process lifetime. Tickers are capped at ``MAX_TRACKED_TICKERS`` with
least-recently-touched eviction, and a re-seen evicted ticker rebuilds cleanly.
"""

import logging
from datetime import UTC, datetime

from orion.processing import feature_engine as fe_mod
from orion.processing.feature_engine import MAX_TRACKED_TICKERS, FeatureEngine
from orion.storage.models import BronzeEvent


def _bar_event(ticker: str, ts: datetime, close: float = 100.0) -> BronzeEvent:
    return BronzeEvent(
        event_id=f"{ticker}_{ts.isoformat()}",
        source="ALPACA",
        source_event_id=None,
        event_type="ALPACA_BAR_1M",
        ticker=ticker,
        event_ts_utc=ts,
        received_ts_utc=ts,
        payload={
            "symbol": ticker,
            "bar_start_ts_utc": ts.isoformat(),
            "o": close,
            "h": close + 1,
            "l": close - 1,
            "c": close,
            "v": 1000,
            "vw": close,
        },
    )


def _feed_bar(engine: FeatureEngine, ticker: str, ts: datetime, close: float = 100.0) -> None:
    engine._hydrated = True
    engine.process_alpaca_bars([_bar_event(ticker, ts, close)])


def test_history_dict_capped_at_max_tracked_tickers():
    """Feeding bars for MAX_TRACKED_TICKERS+N distinct tickers keeps the
    history dict size <= cap."""
    engine = FeatureEngine()
    engine._hydrated = True
    ts = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)

    n_extra = 25
    for i in range(MAX_TRACKED_TICKERS + n_extra):
        _feed_bar(engine, f"TKR{i}", ts)

    assert len(engine.history) <= MAX_TRACKED_TICKERS
    assert len(engine.history) == MAX_TRACKED_TICKERS


def test_history_evicts_least_recently_updated(caplog):
    """The least-recently-touched ticker is the one evicted when over cap."""
    engine = FeatureEngine()
    engine._hydrated = True
    ts = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)

    # Fill exactly to the cap.
    for i in range(MAX_TRACKED_TICKERS):
        _feed_bar(engine, f"TKR{i}", ts)

    # TKR0 is the least-recently-updated. Touch all others so TKR0 is the LRU
    # victim, then insert one new ticker to force a single eviction.
    for i in range(1, MAX_TRACKED_TICKERS):
        _feed_bar(engine, f"TKR{i}", ts)

    with caplog.at_level(logging.INFO):
        _feed_bar(engine, "NEWTKR", ts)

    assert "TKR0" not in engine.history
    assert "NEWTKR" in engine.history
    assert len(engine.history) == MAX_TRACKED_TICKERS
    # Eviction logged at INFO with the evicted ticker carried in `extra`.
    evictions = [r for r in caplog.records if r.message == "feature_engine_ticker_evicted"]
    assert any(getattr(r, "ticker", None) == "TKR0" for r in evictions)


def test_reseen_evicted_ticker_rebuilds_cleanly():
    """An evicted ticker that is fed bars again rebuilds from scratch (the
    existing cold-start behavior) without error."""
    engine = FeatureEngine()
    engine._hydrated = True
    ts = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)

    _feed_bar(engine, "VICTIM", ts, close=50.0)
    assert "VICTIM" in engine.history

    # Evict VICTIM by overflowing the cap with fresh tickers.
    for i in range(MAX_TRACKED_TICKERS + 5):
        _feed_bar(engine, f"FILL{i}", ts)
    assert "VICTIM" not in engine.history

    # Re-seeing VICTIM rebuilds its history cleanly.
    later = datetime(2025, 1, 1, 11, 0, tzinfo=UTC)
    signals = engine.process_alpaca_bars([_bar_event("VICTIM", later, close=75.0)])
    assert "VICTIM" in engine.history
    assert len(signals) == 1
    assert signals[0].ticker == "VICTIM"
    # History was rebuilt from the single new bar, not the stale pre-eviction one.
    assert len(engine.history["VICTIM"]) == 1


def test_flow_history_capped_and_lru():
    """flow_history uses the same LRU mechanism and respects the cap."""
    engine = FeatureEngine()
    ts = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)

    def _flow(ticker: str) -> BronzeEvent:
        return BronzeEvent(
            event_id=f"flow_{ticker}",
            source="UW",
            source_event_id=None,
            event_type="UW_FLOW",
            ticker=ticker,
            event_ts_utc=ts,
            received_ts_utc=ts,
            payload={"ticker": ticker, "put_call": "C", "premium_usd": 1000.0},
        )

    for i in range(MAX_TRACKED_TICKERS + 10):
        engine.process_uw_flow([_flow(f"FTK{i}")])

    assert len(engine.flow_history) == MAX_TRACKED_TICKERS
    assert isinstance(engine.flow_history, fe_mod._LRUTickerStore)
