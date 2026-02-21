from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import pytest

import orion.main_price_target_labeler as labeler


@pytest.mark.asyncio
async def test_get_gex_at_entry_prefers_heber_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_greek_exposure(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {"ts_utc": entry_ts - timedelta(minutes=10), "gex_oi": 100.0, "vex_oi": 10.0},
                    {"ts_utc": entry_ts - timedelta(minutes=1), "gex_oi": 125.0, "vex_oi": 12.5},
                ]
            )

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = await labeler.get_gex_at_entry("AAPL", entry_ts)

    assert result == {"gex": 125.0, "vex": 12.5}


@pytest.mark.asyncio
async def test_get_gex_at_entry_returns_none_when_heber_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_greek_exposure(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = await labeler.get_gex_at_entry("AAPL", entry_ts)

    assert result == {"gex": None, "vex": None}


@pytest.mark.asyncio
async def test_get_gex_rolling_averages_prefers_heber_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_greek_exposure(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {"ts_utc": entry_ts - timedelta(days=2), "gex_oi": 100.0, "vex_oi": 50.0},
                    {"ts_utc": entry_ts - timedelta(days=1), "gex_oi": 120.0, "vex_oi": 70.0},
                ]
            )

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = await labeler.get_gex_rolling_averages("AAPL", entry_ts, days=3)

    assert result == {"gex_rolling_avg": 110.0, "vex_rolling_avg": 60.0}


@pytest.mark.asyncio
async def test_get_gex_rolling_averages_returns_none_when_heber_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_greek_exposure(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = await labeler.get_gex_rolling_averages("AAPL", entry_ts, days=20)

    assert result == {"gex_rolling_avg": None, "vex_rolling_avg": None}


@pytest.mark.asyncio
async def test_get_market_tide_before_entry_prefers_heber_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_market_tide(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {"ts_utc": entry_ts - timedelta(minutes=20), "net_call_premium": 300.0, "net_put_premium": -100.0},
                    {"ts_utc": entry_ts - timedelta(minutes=5), "net_call_premium": 50.0, "net_put_premium": -10.0},
                ]
            )

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = labeler.get_market_tide_before_entry(entry_ts, minutes=30)

    assert result["net_premium"] == 240.0
    assert result["direction"] == "BULLISH"


@pytest.mark.asyncio
async def test_get_market_tide_before_entry_returns_none_when_heber_shape_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_market_tide(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame([{"ts_utc": entry_ts - timedelta(minutes=10), "unexpected": 1}])

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = labeler.get_market_tide_before_entry(entry_ts, minutes=30)

    assert result == {"net_premium": None, "direction": None}


@pytest.mark.asyncio
async def test_get_darkpool_volume_prefers_heber_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_darkpool(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {"dark_ts_utc": entry_ts - timedelta(minutes=50), "size_shares": 100},
                    {"dark_ts_utc": entry_ts - timedelta(minutes=10), "size_shares": 250},
                    {"dark_ts_utc": entry_ts - timedelta(minutes=70), "size_shares": 999},
                ]
            )

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = await labeler.get_darkpool_volume("AAPL", entry_ts, window_minutes=60)
    assert result == 350.0


@pytest.mark.asyncio
async def test_get_darkpool_volume_returns_none_when_heber_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_darkpool(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = await labeler.get_darkpool_volume("AAPL", entry_ts, window_minutes=60)
    assert result is None


@pytest.mark.asyncio
async def test_get_rvol_metrics_prefers_heber_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_bars(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {"ts_event": entry_ts - timedelta(minutes=20), "volume": 100},
                    {"ts_event": entry_ts.replace(hour=10, minute=0), "volume": 200},
                    {"ts_event": entry_ts - timedelta(days=1, hours=3), "volume": 300},
                    {"ts_event": entry_ts - timedelta(days=8, hours=2), "volume": 500},
                ]
            )

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = labeler.get_rvol_metrics("AAPL", entry_ts)

    assert result["rvol_1h"] is not None
    assert result["rvol_daily"] is not None
    assert result["rvol_weekly"] is not None


@pytest.mark.asyncio
async def test_get_rvol_metrics_returns_none_when_heber_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_bars(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = labeler.get_rvol_metrics("AAPL", entry_ts)

    assert result == {
        "rvol_1h": None,
        "rvol_daily": None,
        "rvol_weekly": None,
        "rvol_30m": None,
        "rvol_3d": None,
        "rvol_monthly": None,
    }


@pytest.mark.asyncio
async def test_get_window_features_at_entry_builds_period_windows_from_heber(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 0, tzinfo=timezone.utc)
    flow_df = pd.DataFrame(
        {
            "instrument_key": ["equity:AAPL", "equity:AAPL", "equity:AAPL", "equity:MSFT"],
            "ts_event": [
                "2026-02-11T14:30:00Z",
                "2026-02-11T08:00:00Z",
                "2026-02-06T16:00:00Z",
                "2026-02-11T14:30:00Z",
            ],
            "put_call": ["C", "P", "C", "C"],
            "premium_usd": [100.0, 40.0, 60.0, 999.0],
            "is_sweep": [True, False, True, True],
            "aggressor": ["ASK", "BID", "ASK", "ASK"],
        }
    )
    darkpool_df = pd.DataFrame(
        {
            "instrument_key": ["equity:AAPL", "equity:AAPL", "equity:AAPL", "equity:MSFT"],
            "ts_event": [
                "2026-02-11T14:20:00Z",
                "2026-02-11T10:00:00Z",
                "2026-02-08T12:00:00Z",
                "2026-02-11T14:20:00Z",
            ],
            "size_shares": [1000.0, 200.0, 300.0, 9999.0],
        }
    )

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return flow_df

        def read_darkpool(self, **_kwargs: Any) -> pd.DataFrame:
            return darkpool_df

    async def _fail_db_query(_query: Any) -> Any:
        raise AssertionError("local db_query should not be used for window features")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "db_query", _fail_db_query, raising=False)

    result = await labeler.get_window_features_at_entry("AAPL", entry_ts)

    assert result["1h"]["flow_count"] == 1
    assert result["1h"]["call_put_imbalance"] == pytest.approx(1.0)
    assert result["1h"]["dp_volume"] == pytest.approx(1000.0)
    assert result["1d"]["flow_count"] == 2
    assert result["1d"]["call_put_ratio"] == pytest.approx(2.5)
    assert result["1d"]["dp_volume"] == pytest.approx(1200.0)
    assert result["1w"]["flow_count"] == 3
    assert result["1w"]["call_put_ratio"] == pytest.approx(4.0)
    assert result["1w"]["sweep_count"] == 2
    assert result["1w"]["dp_volume"] == pytest.approx(1500.0)


@pytest.mark.asyncio
async def test_get_window_features_at_entry_returns_empty_dict_when_heber_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            raise RuntimeError("boom")

        def read_darkpool(self, **_kwargs: Any) -> pd.DataFrame:
            raise RuntimeError("boom")

    async def _fail_db_query(_query: Any) -> Any:
        raise AssertionError("local db_query should not be used for window features")

    monkeypatch.setattr(labeler, "_heber_reader", _FailingHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "db_query", _fail_db_query, raising=False)

    result = await labeler.get_window_features_at_entry("AAPL", datetime(2026, 2, 11, 15, 0, tzinfo=timezone.utc))

    assert result == {}


@pytest.mark.asyncio
async def test_get_velocity_backfill_candidates_is_decommissioned_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fail_db_query(_fn: Any) -> Any:
        raise AssertionError("local db_query should not run for decommissioned velocity backfill")

    monkeypatch.setattr(labeler, "db_query", _fail_db_query, raising=False)

    records = await labeler.get_velocity_backfill_candidates(
        limit=12,
        after_entry_ts=datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc),
        after_event_id="vel-120",
    )

    assert records == []


@pytest.mark.asyncio
async def test_get_checkpoint_backfill_candidates_is_decommissioned_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fail_db_query(_fn: Any) -> Any:
        raise AssertionError("local db_query should not run for decommissioned checkpoint backfill")

    monkeypatch.setattr(labeler, "db_query", _fail_db_query, raising=False)

    records = await labeler.get_checkpoint_backfill_candidates(
        limit=20,
        after_entry_ts=datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc),
        after_event_id="cp-120",
    )

    assert records == []


@pytest.mark.asyncio
async def test_get_sector_correlation_features_prefers_heber_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "ts_event": entry_ts - timedelta(minutes=20),
                        "ticker": "AAPL",
                        "put_call": "C",
                        "premium_usd": 1_500_000,
                    },
                    {
                        "ts_event": entry_ts - timedelta(minutes=10),
                        "ticker": "MSFT",
                        "put_call": "P",
                        "premium_usd": 100_000,
                    },
                    {
                        "ts_event": entry_ts - timedelta(hours=2),
                        "ticker": "AAPL",
                        "put_call": "C",
                        "premium_usd": 999_999,
                    },
                ]
            )

        def read_bars(self, *, symbols: list[str], **_kwargs: Any) -> pd.DataFrame:
            if symbols == ["SPY"]:
                return pd.DataFrame(
                    [
                        {"ts_event": entry_ts - timedelta(minutes=70), "symbol": "SPY", "close": 100.0},
                        {"ts_event": entry_ts - timedelta(minutes=5), "symbol": "SPY", "close": 101.0},
                    ]
                )

            return pd.DataFrame(
                [
                    {"ts_event": datetime(2026, 2, 6, 20, 0, tzinfo=timezone.utc), "symbol": "AAPL", "close": 100.0},
                    {"ts_event": datetime(2026, 2, 7, 20, 0, tzinfo=timezone.utc), "symbol": "AAPL", "close": 102.0},
                    {"ts_event": datetime(2026, 2, 8, 20, 0, tzinfo=timezone.utc), "symbol": "AAPL", "close": 104.0},
                    {"ts_event": datetime(2026, 2, 9, 20, 0, tzinfo=timezone.utc), "symbol": "AAPL", "close": 106.0},
                    {"ts_event": datetime(2026, 2, 10, 20, 0, tzinfo=timezone.utc), "symbol": "AAPL", "close": 108.0},
                    {"ts_event": datetime(2026, 2, 6, 20, 0, tzinfo=timezone.utc), "symbol": "SPY", "close": 200.0},
                    {"ts_event": datetime(2026, 2, 7, 20, 0, tzinfo=timezone.utc), "symbol": "SPY", "close": 202.0},
                    {"ts_event": datetime(2026, 2, 8, 20, 0, tzinfo=timezone.utc), "symbol": "SPY", "close": 204.0},
                    {"ts_event": datetime(2026, 2, 9, 20, 0, tzinfo=timezone.utc), "symbol": "SPY", "close": 206.0},
                    {"ts_event": datetime(2026, 2, 10, 20, 0, tzinfo=timezone.utc), "symbol": "SPY", "close": 208.0},
                ]
            )

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = await labeler.get_sector_correlation_features("AAPL", entry_ts)

    assert result["sector_net_premium_1h"] == 1_400_000.0
    assert result["sector_flow_direction"] == "BULLISH"
    assert result["spy_return_1h"] == pytest.approx(1.0)
    assert result["spy_correlation_5d"] == pytest.approx(1.0, rel=1e-3)


@pytest.mark.asyncio
async def test_get_sector_correlation_features_returns_none_when_heber_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

        def read_bars(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = await labeler.get_sector_correlation_features("AAPL", entry_ts)

    assert result == {
        "sector_net_premium_1h": None,
        "sector_flow_direction": None,
        "spy_correlation_5d": None,
        "spy_return_1h": None,
    }


@pytest.mark.asyncio
async def test_get_ticker_info_returns_defaults_without_db_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker = "ZZZZ"
    labeler._ticker_info_cache.pop(ticker, None)
    calls = {"db_query": 0}

    async def _record_db_query(_callback):
        calls["db_query"] += 1
        return None

    monkeypatch.setattr(labeler, "db_query", _record_db_query, raising=False)
    monkeypatch.setattr(labeler, "_get_uw_client", lambda: None, raising=False)

    result = await labeler.get_ticker_info(ticker)

    assert result == {
        "sector": None,
        "next_earnings_date": None,
        "announce_time": None,
        "last_earnings_date": None,
    }
    assert calls["db_query"] == 0


@pytest.mark.asyncio
async def test_get_opposing_flow_prefers_heber_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)
    end_ts = entry_ts + timedelta(hours=2)

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "ts_event": entry_ts + timedelta(minutes=5),
                        "ticker": "AAPL",
                        "put_call": "P",
                        "premium_usd": 200_000,
                        "is_sweep": True,
                        "aggressor": "ASK",
                    },
                    {
                        "ts_event": entry_ts + timedelta(minutes=10),
                        "ticker": "AAPL",
                        "put_call": "P",
                        "premium_usd": 999_999,
                        "is_sweep": False,
                        "aggressor": "ASK",
                    },
                    {
                        "ts_event": entry_ts + timedelta(minutes=20),
                        "ticker": "AAPL",
                        "put_call": "P",
                        "premium_usd": 300_000,
                        "is_sweep": True,
                        "aggressor": "ASK",
                    },
                    {
                        "ts_event": entry_ts + timedelta(hours=3),
                        "ticker": "AAPL",
                        "put_call": "P",
                        "premium_usd": 400_000,
                        "is_sweep": True,
                        "aggressor": "ASK",
                    },
                    {
                        "ts_event": entry_ts + timedelta(minutes=7),
                        "ticker": "AAPL",
                        "put_call": "C",
                        "premium_usd": 500_000,
                        "is_sweep": True,
                        "aggressor": "ASK",
                    },
                ]
            )

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = await labeler.get_opposing_flow("AAPL", "C", entry_ts, end_ts)

    assert result == {"count": 2, "premium": 500_000.0}


@pytest.mark.asyncio
async def test_get_opposing_flow_returns_zeroes_when_heber_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)
    end_ts = entry_ts + timedelta(hours=2)

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = await labeler.get_opposing_flow("AAPL", "C", entry_ts, end_ts)

    assert result == {"count": 0, "premium": 0.0}


@pytest.mark.asyncio
async def test_get_flow_aggression_prefers_heber_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "ts_event": entry_ts - timedelta(minutes=45),
                        "ticker": "AAPL",
                        "aggressor": "ASK",
                        "is_sweep": True,
                        "premium_usd": 100_000,
                    },
                    {
                        "ts_event": entry_ts - timedelta(minutes=20),
                        "ticker": "AAPL",
                        "aggressor": "BID",
                        "is_sweep": False,
                        "premium_usd": 200_000,
                    },
                    {
                        "ts_event": entry_ts - timedelta(minutes=5),
                        "ticker": "AAPL",
                        "aggressor": "ASK",
                        "is_sweep": True,
                        "premium_usd": 50_000,
                    },
                    {
                        "ts_event": entry_ts - timedelta(minutes=90),
                        "ticker": "AAPL",
                        "aggressor": "ASK",
                        "is_sweep": True,
                        "premium_usd": 999_999,
                    },
                    {
                        "ts_event": entry_ts - timedelta(minutes=10),
                        "ticker": "MSFT",
                        "aggressor": "ASK",
                        "is_sweep": True,
                        "premium_usd": 123_456,
                    },
                ]
            )

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = await labeler.get_flow_aggression("AAPL", entry_ts)

    assert result["ask_side_ratio"] == pytest.approx(2 / 3)
    assert result["sweep_ratio_1h"] == pytest.approx(2 / 3)
    assert result["same_ticker_premium_1h"] == pytest.approx(350_000.0)


@pytest.mark.asyncio
async def test_get_flow_aggression_returns_none_when_heber_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = await labeler.get_flow_aggression("AAPL", entry_ts)

    assert result == {
        "ask_side_ratio": None,
        "sweep_ratio_1h": None,
        "same_ticker_premium_1h": None,
    }


@pytest.mark.asyncio
async def test_get_institutional_flow_1w_prefers_heber_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {"ts_event": entry_ts - timedelta(days=1), "ticker": "AAPL", "premium_usd": 120_000},
                    {"ts_event": entry_ts - timedelta(days=2), "ticker": "AAPL", "premium_usd": 80_000},
                    {"ts_event": entry_ts - timedelta(days=3), "ticker": "AAPL", "premium_usd": 10_000},
                    {"ts_event": entry_ts - timedelta(days=8), "ticker": "AAPL", "premium_usd": 500_000},
                    {"ts_event": entry_ts - timedelta(days=1), "ticker": "MSFT", "premium_usd": 999_999},
                ]
            )

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = await labeler.get_institutional_flow_1w("AAPL", entry_ts)

    assert result == pytest.approx(200_000.0)


@pytest.mark.asyncio
async def test_get_institutional_flow_1w_returns_none_when_heber_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = await labeler.get_institutional_flow_1w("AAPL", entry_ts)

    assert result is None


@pytest.mark.asyncio
async def test_get_phase1_bucket_features_prefers_heber_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_bars(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "ts_event": datetime(2026, 2, 6, 20, 0, tzinfo=timezone.utc),
                        "open": 90.0,
                        "close": 90.0,
                        "vwap": 90.0,
                    },
                    {
                        "ts_event": datetime(2026, 2, 10, 20, 0, tzinfo=timezone.utc),
                        "open": 100.0,
                        "close": 100.0,
                        "vwap": 100.0,
                    },
                    {
                        "ts_event": datetime(2026, 2, 11, 14, 30, tzinfo=timezone.utc),
                        "open": 102.0,
                        "close": 103.0,
                        "vwap": 101.0,
                    },
                    {
                        "ts_event": datetime(2026, 2, 11, 15, 0, tzinfo=timezone.utc),
                        "open": 103.0,
                        "close": 104.0,
                        "vwap": 102.0,
                    },
                ]
            )

    async def _fake_ticker_info(_ticker: str):
        return {}

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "get_ticker_info", _fake_ticker_info, raising=False)

    result = await labeler.get_phase1_bucket_features("AAPL", entry_ts, dte=5)

    assert result["minutes_to_close"] == 270
    assert result["overnight_gap_pct"] == pytest.approx(2.0)
    assert result["vwap_distance_pct"] == pytest.approx(((104.0 - 102.0) / 102.0) * 100)
    assert result["price_change_5d_prior"] == pytest.approx(((100.0 - 90.0) / 90.0) * 100)


@pytest.mark.asyncio
async def test_get_phase1_bucket_features_returns_none_when_heber_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_bars(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    async def _fake_ticker_info(_ticker: str):
        return {}

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "get_ticker_info", _fake_ticker_info, raising=False)

    result = await labeler.get_phase1_bucket_features("AAPL", entry_ts, dte=5)

    assert result["minutes_to_close"] == 270
    assert result["overnight_gap_pct"] is None
    assert result["price_change_5d_prior"] is None
    assert result["vwap_distance_pct"] is None
    assert result["earnings_in_dte_window"] is None


@pytest.mark.asyncio
async def test_get_p2_features_prefers_heber_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)
    option_chain = "AAPL250221C00190000"

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "ts_event": datetime(2026, 2, 10, 15, 0, tzinfo=timezone.utc),
                        "option_chain": option_chain,
                        "open_interest": 110,
                        "iv": 0.24,
                    },
                    {
                        "ts_event": datetime(2026, 2, 11, 15, 0, tzinfo=timezone.utc),
                        "option_chain": option_chain,
                        "open_interest": 120,
                        "iv": 0.28,
                    },
                    {
                        "ts_event": datetime(2026, 2, 11, 16, 0, tzinfo=timezone.utc),
                        "option_chain": option_chain,
                        "open_interest": 130,
                        "iv": 0.30,
                    },
                    {
                        "ts_event": datetime(2026, 2, 11, 15, 0, tzinfo=timezone.utc),
                        "option_chain": "MSFT250221C00400000",
                        "open_interest": 999,
                        "iv": 0.99,
                    },
                ]
            )

        def read_bars(self, **_kwargs: Any) -> pd.DataFrame:
            close_values = [100.0, 102.0, 101.0, 103.0, 105.0, 104.0, 107.0, 106.0, 108.0, 111.0, 109.0, 112.0]
            start_day = datetime(2026, 1, 30, 20, 0, tzinfo=timezone.utc)
            rows = []
            for idx, close in enumerate(close_values):
                rows.append({"ts_event": start_day + timedelta(days=idx), "symbol": "AAPL", "close": close})
            return pd.DataFrame(rows)

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = await labeler.get_p2_features("AAPL", option_chain, entry_ts)

    import statistics

    closes = [100.0, 102.0, 101.0, 103.0, 105.0, 104.0, 107.0, 106.0, 108.0, 111.0, 109.0, 112.0]
    returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
    expected_hv = statistics.stdev(returns) * (252**0.5) * 100
    expected_ratio = 30.0 / expected_hv

    assert result["oi_change_1d"] == pytest.approx(10.0)
    assert result["oi_change_pct"] == pytest.approx((10.0 / 120.0) * 100.0)
    assert result["hv_30d"] == pytest.approx(expected_hv)
    assert result["iv_vs_hv_ratio"] == pytest.approx(expected_ratio)


@pytest.mark.asyncio
async def test_get_p2_features_returns_none_when_heber_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)
    option_chain = "AAPL250221C00190000"

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

        def read_bars(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = await labeler.get_p2_features("AAPL", option_chain, entry_ts)

    assert result == {
        "oi_change_1d": None,
        "oi_change_pct": None,
        "iv_vs_hv_ratio": None,
        "hv_30d": None,
    }


@pytest.mark.asyncio
async def test_get_p3_features_prefers_heber_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)
    expiry = datetime(2026, 2, 21, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_bars(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "ts_event": datetime(2026, 2, 1, 20, 0, tzinfo=timezone.utc),
                        "symbol": "AAPL",
                        "high": 150.0,
                        "close": 140.0,
                    },
                    {
                        "ts_event": datetime(2026, 2, 10, 20, 0, tzinfo=timezone.utc),
                        "symbol": "AAPL",
                        "high": 160.0,
                        "close": 155.0,
                    },
                    {
                        "ts_event": datetime(2026, 2, 11, 15, 0, tzinfo=timezone.utc),
                        "symbol": "AAPL",
                        "high": 158.0,
                        "close": 150.0,
                    },
                    {
                        "ts_event": datetime(2026, 2, 11, 15, 10, tzinfo=timezone.utc),
                        "symbol": "MSFT",
                        "high": 500.0,
                        "close": 500.0,
                    },
                ]
            )

        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "ts_event": datetime(2026, 2, 11, 15, 5, tzinfo=timezone.utc),
                        "ticker": "AAPL",
                        "expiry": "2026-02-21",
                    },
                    {
                        "ts_event": datetime(2026, 2, 11, 15, 20, tzinfo=timezone.utc),
                        "ticker": "AAPL",
                        "expiry": "2026-02-21",
                    },
                    {
                        "ts_event": datetime(2026, 2, 11, 14, 20, tzinfo=timezone.utc),
                        "ticker": "AAPL",
                        "expiry": "2026-02-21",
                    },
                    {
                        "ts_event": datetime(2026, 2, 11, 15, 10, tzinfo=timezone.utc),
                        "ticker": "AAPL",
                        "expiry": "2026-02-28",
                    },
                ]
            )

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = await labeler.get_p3_features("AAPL", "AAPL250221C00190000", expiry, entry_ts)

    assert result["high_52w_distance_pct"] == pytest.approx(((160.0 - 150.0) / 160.0) * 100.0)
    assert result["same_expiry_trades_1h"] == 2
    assert result["is_spread_leg"] is True


@pytest.mark.asyncio
async def test_get_p3_features_returns_none_when_heber_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)
    expiry = datetime(2026, 2, 21, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_bars(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = await labeler.get_p3_features("AAPL", "AAPL250221C00190000", expiry, entry_ts)

    assert result == {
        "high_52w_distance_pct": None,
        "is_spread_leg": None,
        "same_expiry_trades_1h": None,
    }


@pytest.mark.asyncio
async def test_get_flow_greeks_prefers_heber_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    event_id = "evt-123"
    flow_ts = datetime(2026, 2, 11, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "event_id": event_id,
                        "ts_event": flow_ts,
                        "option_chain": "AAPL250221C00190000",
                        "volume_contract": 100,
                        "open_interest": 200,
                        "iv": 0.35,
                        "underlying_price": 190.0,
                        "strike": 190.0,
                        "put_call": "C",
                        "expiry": "2026-02-21",
                        "delta_alpaca": 0.55,
                        "gamma_alpaca": 0.02,
                        "theta_alpaca": -0.10,
                        "vega_alpaca": 0.11,
                        "rho_alpaca": 0.03,
                        "iv_alpaca": 0.36,
                    }
                ]
            )

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = await labeler.get_flow_greeks(event_id)

    assert result["delta"] == pytest.approx(0.55)
    assert result["gamma"] == pytest.approx(0.02)
    assert result["theta"] == pytest.approx(-0.10)
    assert result["vega"] == pytest.approx(0.11)
    assert result["rho"] == pytest.approx(0.03)
    assert result["iv"] == pytest.approx(0.35)
    assert result["iv_alpaca"] == pytest.approx(0.36)
    assert result["volume"] == pytest.approx(100)
    assert result["open_interest"] == pytest.approx(200)


@pytest.mark.asyncio
async def test_get_flow_greeks_returns_null_payload_when_heber_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_id = "evt-999"

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    result = await labeler.get_flow_greeks(event_id)

    assert result == {
        "delta": None,
        "gamma": None,
        "theta": None,
        "vega": None,
        "rho": None,
        "volume": None,
        "open_interest": None,
        "iv": None,
        "iv_alpaca": None,
    }


@pytest.mark.asyncio
async def test_get_real_checkpoint_prices_prefers_heber_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_id = "evt-abc"

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "event_id": event_id,
                        "checkpoint": "15m",
                        "mid_price": 1.25,
                        "last_trade_price": 1.20,
                        "delta": 0.55,
                        "gamma": 0.02,
                        "theta": -0.10,
                        "vega": 0.11,
                        "iv": 0.31,
                    },
                    {
                        "event_id": event_id,
                        "checkpoint": "1h",
                        "mid_price": None,
                        "last_trade_price": 1.50,
                        "delta": 0.60,
                        "gamma": 0.03,
                        "theta": -0.08,
                        "vega": 0.13,
                        "iv": 0.33,
                    },
                ]
            )

    async def _fail_db_query(_callback):
        raise AssertionError("db_query should not run when Heber checkpoint data is available")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "db_query", _fail_db_query, raising=False)

    result = await labeler.get_real_checkpoint_prices(event_id)

    assert result == {
        "15m": {
            "price": 1.25,
            "delta": 0.55,
            "gamma": 0.02,
            "theta": -0.10,
            "vega": 0.11,
            "iv": 0.31,
        },
        "1h": {
            "price": 1.50,
            "delta": 0.60,
            "gamma": 0.03,
            "theta": -0.08,
            "vega": 0.13,
            "iv": 0.33,
        },
    }


@pytest.mark.asyncio
async def test_get_real_checkpoint_prices_returns_empty_when_heber_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    async def _fail_db_query(_callback):
        raise AssertionError("db_query should not run when Heber checkpoint data is unavailable")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "db_query", _fail_db_query, raising=False)

    result = await labeler.get_real_checkpoint_prices("evt-none")

    assert result == {}
