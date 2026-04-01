from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from orion.jobs import data_quality_checker as dqc


@pytest.mark.asyncio
async def test_get_flow_summary_prefers_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    flow_df = pd.DataFrame(
        {
            "instrument_key": ["equity:SPY", "equity:QQQ", "equity:SPY"],
            "ts_event": [now - timedelta(minutes=10), now - timedelta(minutes=7), now - timedelta(minutes=2)],
            "premium_usd": [100.0, 0.0, 200.0],
        }
    )

    class _FakeReader:
        def read_flow(self, **_kwargs):
            return flow_df

    monkeypatch.delenv("ORION_DATA_QUALITY_CHECKER_PREFER_HEBER", raising=False)
    monkeypatch.setattr(dqc, "get_heber_reader", lambda: _FakeReader())

    async def _fail_db_query(_fn):
        raise AssertionError("local fallback should not be called")

    monkeypatch.setattr(dqc, "db_query", _fail_db_query, raising=False)

    summary = await dqc.get_flow_summary()

    assert summary["backend"] == "heber"
    assert summary["total_flows_24h"] == 3
    assert summary["valid_premium"] == 2
    assert summary["missing_premium"] == 1
    assert summary["unique_tickers"] == 2


@pytest.mark.asyncio
async def test_get_flow_summary_returns_empty_when_heber_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeReader:
        def read_flow(self, **_kwargs):
            raise RuntimeError("heber unavailable")

    monkeypatch.setattr(dqc, "get_heber_reader", lambda: _FakeReader())

    async def _fail_db_query(_fn):
        raise AssertionError("local fallback should not be called")

    monkeypatch.setattr(dqc, "db_query", _fail_db_query, raising=False)

    summary = await dqc.get_flow_summary()

    assert summary["backend"] in {"heber_unavailable", "source_unavailable"}
    assert summary["total_flows_24h"] == 0


@pytest.mark.asyncio
async def test_check_flow_staleness_uses_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    flow_df = pd.DataFrame(
        {
            "instrument_key": ["equity:SPY"],
            "ts_event": [now - timedelta(minutes=40)],
            "premium_usd": [100.0],
        }
    )

    class _FakeReader:
        def read_flow(self, **_kwargs):
            return flow_df

    monkeypatch.setattr(dqc, "_is_market_hours", lambda *_args, **_kwargs: True, raising=False)
    monkeypatch.setattr(dqc, "get_heber_reader", lambda: _FakeReader())

    stale = await dqc.check_flow_staleness(stale_minutes=30)
    assert stale is True


@pytest.mark.asyncio
async def test_get_darkpool_summary_prefers_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    darkpool_df = pd.DataFrame(
        {
            "instrument_key": ["equity:SPY", "equity:QQQ"],
            "ts_event": [now - timedelta(minutes=20), now - timedelta(minutes=5)],
            "size_shares": [1000, 0],
            "trade_price": [10.0, 0.0],
        }
    )

    class _FakeReader:
        def read_darkpool(self, **_kwargs):
            return darkpool_df

    monkeypatch.setattr(dqc, "get_heber_reader", lambda: _FakeReader())

    async def _fail_db_query(_fn):
        raise AssertionError("local fallback should not be called")

    monkeypatch.setattr(dqc, "db_query", _fail_db_query, raising=False)

    summary = await dqc.get_darkpool_summary()

    assert summary["backend"] == "heber"
    assert summary["total_trades_24h"] == 2
    assert summary["valid_trades"] == 1
    assert summary["invalid_price"] == 1
    assert summary["unique_tickers"] == 2


@pytest.mark.asyncio
async def test_get_bars_summary_prefers_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    bars_df = pd.DataFrame(
        {
            "instrument_key": ["equity:SPY", "equity:QQQ", "equity:SPY"],
            "ts_event": [now - timedelta(minutes=12), now - timedelta(minutes=8), now - timedelta(minutes=2)],
            "close": [600.0, 0.0, 602.0],
        }
    )

    class _FakeReader:
        def read_bars(self, **_kwargs):
            return bars_df

    monkeypatch.setattr(dqc, "get_heber_reader", lambda: _FakeReader())

    async def _fail_db_query(_fn):
        raise AssertionError("local fallback should not be called")

    monkeypatch.setattr(dqc, "db_query", _fail_db_query, raising=False)

    summary = await dqc.get_bars_summary()

    assert summary["backend"] == "heber"
    assert summary["total_bars_24h"] == 3
    assert summary["valid_bars"] == 2
    assert summary["invalid_bars"] == 1
    assert summary["unique_tickers"] == 2
    assert summary["validity_pct"] == pytest.approx(66.67, abs=0.01)


@pytest.mark.asyncio
async def test_check_data_staleness_prefers_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    bars_df = pd.DataFrame(
        {
            "instrument_key": ["equity:SPY", "equity:QQQ", "equity:QQQ"],
            "ts_event": [now - timedelta(minutes=45), now - timedelta(minutes=5), now - timedelta(minutes=2)],
            "close": [600.0, 500.0, 501.0],
        }
    )

    class _FakeReader:
        def read_bars(self, **_kwargs):
            return bars_df

    monkeypatch.setattr(dqc, "_is_market_hours", lambda *_args, **_kwargs: True, raising=False)
    monkeypatch.setattr(dqc, "get_heber_reader", lambda: _FakeReader())
    monkeypatch.setattr(dqc, "CRITICAL_TICKERS", ["SPY", "QQQ"])

    stale = await dqc.check_data_staleness(stale_minutes=15)

    assert len(stale) == 1
    assert stale[0]["ticker"] == "SPY"


@pytest.mark.asyncio
async def test_check_bar_gaps_prefers_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    bars_df = pd.DataFrame(
        {
            "instrument_key": ["equity:SPY", "equity:SPY", "equity:SPY"],
            "ts_event": [now - timedelta(minutes=20), now - timedelta(minutes=19), now - timedelta(minutes=10)],
            "close": [600.0, 601.0, 602.0],
        }
    )

    class _FakeReader:
        def read_bars(self, **_kwargs):
            return bars_df

    monkeypatch.setattr(dqc, "_is_market_hours", lambda *_args, **_kwargs: True, raising=False)
    monkeypatch.setattr(dqc, "get_heber_reader", lambda: _FakeReader())

    gaps = await dqc.check_bar_gaps(ticker="SPY", gap_minutes=5)

    assert gaps
    assert gaps[0]["gap_minutes"] > 5


@pytest.mark.asyncio
async def test_check_flow_staleness_uses_market_schedule_when_dst_opens_before_14_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = datetime(2026, 6, 1, 13, 45, tzinfo=UTC)
    flow_df = pd.DataFrame(
        {
            "instrument_key": ["equity:SPY"],
            "ts_event": [fixed_now - timedelta(minutes=40)],
            "premium_usd": [100.0],
        }
    )

    class _FakeReader:
        def read_flow(self, **_kwargs):
            return flow_df

    class _Schedule:
        def is_market_open(self, timestamp):
            assert timestamp == fixed_now
            return True

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            _ = tz
            return fixed_now

    monkeypatch.setattr(dqc, "datetime", _FrozenDateTime)
    monkeypatch.setattr(dqc, "MarketSchedule", lambda: _Schedule(), raising=False)
    monkeypatch.setattr(dqc, "get_heber_reader", lambda: _FakeReader())

    stale = await dqc.check_flow_staleness(stale_minutes=30)

    assert stale is True


@pytest.mark.asyncio
async def test_check_flow_staleness_returns_none_when_heber_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeReader:
        def read_flow(self, **_kwargs):
            raise RuntimeError("heber unavailable")

    monkeypatch.setattr(dqc, "_is_market_hours", lambda *_args, **_kwargs: True, raising=False)
    monkeypatch.setattr(dqc, "get_heber_reader", lambda: _FakeReader())

    stale = await dqc.check_flow_staleness(stale_minutes=30)

    assert stale is None


@pytest.mark.asyncio
async def test_run_quality_checks_warns_when_flow_backend_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _fake_init_db() -> None:
        return None

    async def _fake_get_bars_summary() -> dict:
        return {
            "total_bars_24h": 0,
            "validity_pct": 100,
            "unique_tickers": 0,
            "backend": "heber",
        }

    async def _fake_check_zero_valued_bars(*_args, **_kwargs) -> list[dict]:
        return []

    async def _fake_check_data_staleness(*_args, **_kwargs) -> list[dict]:
        return []

    async def _fake_check_bar_gaps(*_args, **_kwargs) -> list[dict]:
        return []

    async def _fake_get_flow_summary() -> dict:
        return {
            "total_flows_24h": 0,
            "validity_pct": 0,
            "unique_tickers": 0,
            "backend": "heber_unavailable",
        }

    async def _fake_check_flow_staleness(*_args, **_kwargs):
        return None

    async def _fake_get_darkpool_summary() -> dict:
        return {
            "total_trades_24h": 0,
            "validity_pct": 100,
            "unique_tickers": 0,
            "backend": "heber",
        }

    async def _fake_check_darkpool_staleness(*_args, **_kwargs) -> bool:
        return False

    async def _fake_get_ml_features_summary() -> dict:
        return dqc._empty_ml_features_summary(backend="heber")

    async def _fake_check_recent_labels_features() -> dict:
        return dqc._empty_recent_labels_summary(backend="heber")

    monkeypatch.setattr(dqc, "init_db", _fake_init_db)
    monkeypatch.setattr(dqc, "get_bars_summary", _fake_get_bars_summary)
    monkeypatch.setattr(dqc, "check_zero_valued_bars", _fake_check_zero_valued_bars)
    monkeypatch.setattr(dqc, "check_data_staleness", _fake_check_data_staleness)
    monkeypatch.setattr(dqc, "check_bar_gaps", _fake_check_bar_gaps)
    monkeypatch.setattr(dqc, "get_flow_summary", _fake_get_flow_summary)
    monkeypatch.setattr(dqc, "check_flow_staleness", _fake_check_flow_staleness)
    monkeypatch.setattr(dqc, "get_darkpool_summary", _fake_get_darkpool_summary)
    monkeypatch.setattr(dqc, "check_darkpool_staleness", _fake_check_darkpool_staleness)
    monkeypatch.setattr(dqc, "get_ml_features_summary", _fake_get_ml_features_summary)
    monkeypatch.setattr(dqc, "check_recent_labels_features", _fake_check_recent_labels_features)

    with caplog.at_level(logging.WARNING):
        await dqc.run_quality_checks()

    assert "flow data fresh" not in caplog.text.lower()
    assert "unavailable" in caplog.text.lower()


@pytest.mark.asyncio
async def test_get_bars_summary_returns_empty_when_heber_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeReader:
        def read_bars(self, **_kwargs):
            raise RuntimeError("heber unavailable")

    monkeypatch.setattr(dqc, "get_heber_reader", lambda: _FakeReader())

    async def _fail_db_query(_fn):
        raise AssertionError("local fallback should not be called")

    monkeypatch.setattr(dqc, "db_query", _fail_db_query, raising=False)

    summary = await dqc.get_bars_summary()

    assert summary["backend"] in {"heber_unavailable", "source_unavailable"}
    assert summary["total_bars_24h"] == 0


@pytest.mark.asyncio
async def test_get_ml_features_summary_prefers_heber_gold(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    outcomes_df = pd.DataFrame(
        {
            "alert_id": ["a1", "a2", "a3"],
            "underlying": ["AAPL", "MSFT", "QQQ"],
            "ts_event": [now - timedelta(hours=3), now - timedelta(hours=2), now - timedelta(hours=1)],
        }
    )
    features_df = pd.DataFrame(
        {
            "alert_id": ["a1", "a2"],
            "delta": [0.5, 0.4],
            "gamma": [0.1, 0.2],
            "iv": [0.4, 0.35],
            "iv_rank": [60.0, 55.0],
            "volume": [200.0, 300.0],
            "open_interest": [1000.0, 900.0],
            "hour_of_day": [10, 11],
            "minutes_to_close": [120, 80],
            "days_to_expiry": [3, 5],
        }
    )

    class _FakeReader:
        def read_gold_features(self, dataset: str, asof_time, symbols=None):
            _ = (asof_time, symbols)
            if dataset == "labels_alert_barriers":
                return outcomes_df
            if dataset == "meta_label_features":
                return features_df
            raise AssertionError(f"unexpected dataset: {dataset}")

    async def _fail_db_query(_fn):
        raise AssertionError("local db_query should not be called")

    monkeypatch.setattr(dqc, "get_heber_reader", lambda: _FakeReader())
    monkeypatch.setattr(dqc, "db_query", _fail_db_query, raising=False)

    summary = await dqc.get_ml_features_summary()

    assert summary["backend"] == "heber"
    assert summary["total_labels"] == 3
    assert summary["ml_ready_count"] == 2
    assert summary["delta_pct"] == pytest.approx(66.7, abs=0.1)
    assert summary["gamma_pct"] == pytest.approx(66.7, abs=0.1)
    assert summary["iv_rank_pct"] == pytest.approx(66.7, abs=0.1)
    assert summary["entry_hour_pct"] == pytest.approx(66.7, abs=0.1)


@pytest.mark.asyncio
async def test_check_recent_labels_features_prefers_heber_gold(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    outcomes_df = pd.DataFrame(
        {
            "alert_id": ["a1", "a2", "old"],
            "underlying": ["AAPL", "MSFT", "QQQ"],
            "ts_event": [now - timedelta(hours=3), now - timedelta(hours=1), now - timedelta(hours=30)],
        }
    )
    features_df = pd.DataFrame(
        {
            "alert_id": ["a1", "old"],
            "delta": [0.5, 0.2],
            "gamma": [0.1, 0.1],
            "iv_rank": [60.0, 40.0],
        }
    )

    class _FakeReader:
        def read_gold_features(self, dataset: str, asof_time, symbols=None):
            _ = (asof_time, symbols)
            if dataset == "labels_alert_barriers":
                return outcomes_df
            if dataset == "meta_label_features":
                return features_df
            raise AssertionError(f"unexpected dataset: {dataset}")

    async def _fail_db_query(_fn):
        raise AssertionError("local db_query should not be called")

    monkeypatch.setattr(dqc, "get_heber_reader", lambda: _FakeReader())
    monkeypatch.setattr(dqc, "db_query", _fail_db_query, raising=False)

    summary = await dqc.check_recent_labels_features()

    assert summary["backend"] == "heber"
    assert summary["recent_labels"] == 2
    assert summary["ml_ready"] == 1
    assert summary["delta_pct"] == pytest.approx(50.0, abs=0.1)
    assert summary["gamma_pct"] == pytest.approx(50.0, abs=0.1)
    assert summary["iv_rank_pct"] == pytest.approx(50.0, abs=0.1)
