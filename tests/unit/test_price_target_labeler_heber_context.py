from datetime import datetime, timedelta, timezone
from typing import Any

import orion.main_price_target_labeler as labeler
import pandas as pd
import pytest


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

    async def _fail_sql_fallback(_ticker: str, _entry_ts: datetime):
        raise AssertionError("SQL fallback should not run when Heber has usable GEX data")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_gex_at_entry_sql", _fail_sql_fallback, raising=False)

    result = await labeler.get_gex_at_entry("AAPL", entry_ts)

    assert result == {"gex": 125.0, "vex": 12.5}


@pytest.mark.asyncio
async def test_get_gex_at_entry_falls_back_to_sql_when_heber_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_greek_exposure(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    async def _fake_sql_fallback(ticker: str, ts: datetime):
        assert ticker == "AAPL"
        assert ts == entry_ts
        return {"gex": 77.0, "vex": 7.7}

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_gex_at_entry_sql", _fake_sql_fallback, raising=False)

    result = await labeler.get_gex_at_entry("AAPL", entry_ts)

    assert result == {"gex": 77.0, "vex": 7.7}


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

    async def _fail_sql_fallback(_entry_ts: datetime, _minutes: int = 30):
        raise AssertionError("SQL fallback should not run when Heber has usable market tide data")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_market_tide_before_entry_sql", _fail_sql_fallback, raising=False)

    result = await labeler.get_market_tide_before_entry(entry_ts, minutes=30)

    assert result["net_premium"] == 240.0
    assert result["direction"] == "BULLISH"


@pytest.mark.asyncio
async def test_get_market_tide_before_entry_falls_back_to_sql_when_heber_shape_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_market_tide(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame([{"ts_utc": entry_ts - timedelta(minutes=10), "unexpected": 1}])

    async def _fake_sql_fallback(ts: datetime, minutes: int = 30):
        assert ts == entry_ts
        assert minutes == 30
        return {"net_premium": -50.0, "direction": "BEARISH"}

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_market_tide_before_entry_sql", _fake_sql_fallback, raising=False)

    result = await labeler.get_market_tide_before_entry(entry_ts, minutes=30)

    assert result == {"net_premium": -50.0, "direction": "BEARISH"}


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

    async def _fail_sql_fallback(_ticker: str, _entry_ts: datetime, _window_minutes: int = 60):
        raise AssertionError("SQL fallback should not run when Heber has usable darkpool data")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_darkpool_volume_sql", _fail_sql_fallback, raising=False)

    result = await labeler.get_darkpool_volume("AAPL", entry_ts, window_minutes=60)
    assert result == 350.0


@pytest.mark.asyncio
async def test_get_darkpool_volume_falls_back_to_sql_when_heber_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_darkpool(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    async def _fake_sql_fallback(ticker: str, ts: datetime, window_minutes: int = 60):
        assert ticker == "AAPL"
        assert ts == entry_ts
        assert window_minutes == 60
        return 777.0

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_darkpool_volume_sql", _fake_sql_fallback, raising=False)

    result = await labeler.get_darkpool_volume("AAPL", entry_ts, window_minutes=60)
    assert result == 777.0


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

    async def _fail_sql_fallback(_ticker: str, _entry_ts: datetime):
        raise AssertionError("SQL fallback should not run when Heber bars are available")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_rvol_metrics_sql", _fail_sql_fallback, raising=False)

    result = await labeler.get_rvol_metrics("AAPL", entry_ts)

    assert result["rvol_1h"] is not None
    assert result["rvol_daily"] is not None
    assert result["rvol_weekly"] is not None


@pytest.mark.asyncio
async def test_get_rvol_metrics_falls_back_to_sql_when_heber_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_bars(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    expected = {
        "rvol_1h": 1.0,
        "rvol_daily": 1.2,
        "rvol_weekly": 0.9,
        "rvol_30m": 1.0,
        "rvol_3d": 1.2,
        "rvol_monthly": 0.9,
    }

    async def _fake_sql_fallback(ticker: str, ts: datetime):
        assert ticker == "AAPL"
        assert ts == entry_ts
        return expected

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_rvol_metrics_sql", _fake_sql_fallback, raising=False)

    result = await labeler.get_rvol_metrics("AAPL", entry_ts)

    assert result == expected


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

    async def _fail_sql_fallback(_ticker: str, _entry_ts: datetime):
        raise AssertionError("SQL fallback should not run when Heber has usable sector/correlation data")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_sector_correlation_features_sql", _fail_sql_fallback, raising=False)

    result = await labeler.get_sector_correlation_features("AAPL", entry_ts)

    assert result["sector_net_premium_1h"] == 1_400_000.0
    assert result["sector_flow_direction"] == "BULLISH"
    assert result["spy_return_1h"] == pytest.approx(1.0)
    assert result["spy_correlation_5d"] == pytest.approx(1.0, rel=1e-3)


@pytest.mark.asyncio
async def test_get_sector_correlation_features_falls_back_to_sql_when_heber_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

        def read_bars(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    expected = {
        "sector_net_premium_1h": 123.0,
        "sector_flow_direction": "NEUTRAL",
        "spy_correlation_5d": 0.44,
        "spy_return_1h": -0.55,
    }

    async def _fake_sql_fallback(ticker: str, ts: datetime):
        assert ticker == "AAPL"
        assert ts == entry_ts
        return expected

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_sector_correlation_features_sql", _fake_sql_fallback, raising=False)

    result = await labeler.get_sector_correlation_features("AAPL", entry_ts)

    assert result == expected


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

    async def _fail_sql_fallback(_ticker: str, _put_call: str, _entry_ts: datetime, _end_ts: datetime):
        raise AssertionError("SQL fallback should not run when Heber has usable opposing-flow data")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_opposing_flow_sql", _fail_sql_fallback, raising=False)

    result = await labeler.get_opposing_flow("AAPL", "C", entry_ts, end_ts)

    assert result == {"count": 2, "premium": 500_000.0}


@pytest.mark.asyncio
async def test_get_opposing_flow_falls_back_to_sql_when_heber_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)
    end_ts = entry_ts + timedelta(hours=2)

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    expected = {"count": 7, "premium": 777_000.0}

    async def _fake_sql_fallback(ticker: str, put_call: str, ts_start: datetime, ts_end: datetime):
        assert ticker == "AAPL"
        assert put_call == "C"
        assert ts_start == entry_ts
        assert ts_end == end_ts
        return expected

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_opposing_flow_sql", _fake_sql_fallback, raising=False)

    result = await labeler.get_opposing_flow("AAPL", "C", entry_ts, end_ts)

    assert result == expected


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

    async def _fail_sql_fallback(_ticker: str, _entry_ts: datetime):
        raise AssertionError("SQL fallback should not run when Heber has usable flow data")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_flow_aggression_sql", _fail_sql_fallback, raising=False)

    result = await labeler.get_flow_aggression("AAPL", entry_ts)

    assert result["ask_side_ratio"] == pytest.approx(2 / 3)
    assert result["sweep_ratio_1h"] == pytest.approx(2 / 3)
    assert result["same_ticker_premium_1h"] == pytest.approx(350_000.0)


@pytest.mark.asyncio
async def test_get_flow_aggression_falls_back_to_sql_when_heber_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    expected = {"ask_side_ratio": 0.7, "sweep_ratio_1h": 0.5, "same_ticker_premium_1h": 500_000.0}

    async def _fake_sql_fallback(ticker: str, ts: datetime):
        assert ticker == "AAPL"
        assert ts == entry_ts
        return expected

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_flow_aggression_sql", _fake_sql_fallback, raising=False)

    result = await labeler.get_flow_aggression("AAPL", entry_ts)

    assert result == expected


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

    async def _fail_sql_fallback(_ticker: str, _entry_ts: datetime):
        raise AssertionError("SQL fallback should not run when Heber has usable institutional-flow data")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_institutional_flow_1w_sql", _fail_sql_fallback, raising=False)

    result = await labeler.get_institutional_flow_1w("AAPL", entry_ts)

    assert result == pytest.approx(200_000.0)


@pytest.mark.asyncio
async def test_get_institutional_flow_1w_falls_back_to_sql_when_heber_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    async def _fake_sql_fallback(ticker: str, ts: datetime):
        assert ticker == "AAPL"
        assert ts == entry_ts
        return 321_000.0

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_institutional_flow_1w_sql", _fake_sql_fallback, raising=False)

    result = await labeler.get_institutional_flow_1w("AAPL", entry_ts)

    assert result == pytest.approx(321_000.0)


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

    async def _fail_sql_fallback(_ticker: str, _entry_ts: datetime):
        raise AssertionError("SQL fallback should not run when Heber bars are available")

    async def _fake_ticker_info(_ticker: str):
        return {}

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_phase1_bucket_features_sql", _fail_sql_fallback, raising=False)
    monkeypatch.setattr(labeler, "get_ticker_info", _fake_ticker_info, raising=False)

    result = await labeler.get_phase1_bucket_features("AAPL", entry_ts, dte=5)

    assert result["minutes_to_close"] == 270
    assert result["overnight_gap_pct"] == pytest.approx(2.0)
    assert result["vwap_distance_pct"] == pytest.approx(((104.0 - 102.0) / 102.0) * 100)
    assert result["price_change_5d_prior"] == pytest.approx(((100.0 - 90.0) / 90.0) * 100)


@pytest.mark.asyncio
async def test_get_phase1_bucket_features_falls_back_to_sql_when_heber_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_bars(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    async def _fake_sql_fallback(ticker: str, ts: datetime):
        assert ticker == "AAPL"
        assert ts == entry_ts
        return {
            "overnight_gap_pct": 1.5,
            "price_change_5d_prior": 2.5,
            "vwap_distance_pct": -0.5,
        }

    async def _fake_ticker_info(_ticker: str):
        return {}

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_phase1_bucket_features_sql", _fake_sql_fallback, raising=False)
    monkeypatch.setattr(labeler, "get_ticker_info", _fake_ticker_info, raising=False)

    result = await labeler.get_phase1_bucket_features("AAPL", entry_ts, dte=5)

    assert result["minutes_to_close"] == 270
    assert result["overnight_gap_pct"] == pytest.approx(1.5)
    assert result["price_change_5d_prior"] == pytest.approx(2.5)
    assert result["vwap_distance_pct"] == pytest.approx(-0.5)


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

    async def _fail_sql_fallback(_ticker: str, _option_chain: str, _entry_ts: datetime):
        raise AssertionError("SQL fallback should not run when Heber has usable P2 data")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_p2_features_sql", _fail_sql_fallback, raising=False)

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
async def test_get_p2_features_falls_back_to_sql_when_heber_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)
    option_chain = "AAPL250221C00190000"

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

        def read_bars(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    expected = {
        "oi_change_1d": 4.0,
        "oi_change_pct": 2.5,
        "iv_vs_hv_ratio": 1.2,
        "hv_30d": 20.0,
    }

    async def _fake_sql_fallback(ticker: str, option_chain_value: str, ts: datetime):
        assert ticker == "AAPL"
        assert option_chain_value == option_chain
        assert ts == entry_ts
        return expected

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_p2_features_sql", _fake_sql_fallback, raising=False)

    result = await labeler.get_p2_features("AAPL", option_chain, entry_ts)

    assert result == expected


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

    async def _fail_sql_fallback(_ticker: str, _option_chain: str, _expiry: datetime, _entry_ts: datetime):
        raise AssertionError("SQL fallback should not run when Heber has usable P3 data")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_p3_features_sql", _fail_sql_fallback, raising=False)

    result = await labeler.get_p3_features("AAPL", "AAPL250221C00190000", expiry, entry_ts)

    assert result["high_52w_distance_pct"] == pytest.approx(((160.0 - 150.0) / 160.0) * 100.0)
    assert result["same_expiry_trades_1h"] == 2
    assert result["is_spread_leg"] is True


@pytest.mark.asyncio
async def test_get_p3_features_falls_back_to_sql_when_heber_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 30, tzinfo=timezone.utc)
    expiry = datetime(2026, 2, 21, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_bars(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    expected = {
        "high_52w_distance_pct": 5.0,
        "is_spread_leg": False,
        "same_expiry_trades_1h": 1,
    }

    async def _fake_sql_fallback(ticker: str, option_chain: str, expiry_value: datetime, ts: datetime):
        assert ticker == "AAPL"
        assert option_chain == "AAPL250221C00190000"
        assert expiry_value == expiry
        assert ts == entry_ts
        return expected

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_p3_features_sql", _fake_sql_fallback, raising=False)

    result = await labeler.get_p3_features("AAPL", "AAPL250221C00190000", expiry, entry_ts)

    assert result == expected


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

    async def _fail_sql_fallback(_event_id: str):
        raise AssertionError("SQL fallback should not run when Heber has event-level flow Greeks")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_flow_greeks_sql", _fail_sql_fallback, raising=False)

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
async def test_get_flow_greeks_falls_back_to_sql_when_heber_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_id = "evt-999"

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    async def _fake_sql_fallback(received_event_id: str):
        assert received_event_id == event_id
        return {
            "volume": 75,
            "open_interest": 150,
            "iv": 0.25,
            "underlying_price": 100.0,
            "strike": 95.0,
            "put_call": "C",
            "expiry": "2026-03-20",
            "flow_ts": datetime(2026, 2, 11, 14, 0, tzinfo=timezone.utc),
            "option_chain": "AAPL260320C00095000",
            "delta_stored": 0.60,
            "gamma_stored": 0.04,
            "theta_stored": -0.09,
            "vega_stored": 0.12,
            "rho_stored": 0.02,
            "iv_alpaca_stored": 0.26,
        }

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_flow_greeks_sql", _fake_sql_fallback, raising=False)

    result = await labeler.get_flow_greeks(event_id)

    assert result["delta"] == pytest.approx(0.60)
    assert result["gamma"] == pytest.approx(0.04)
    assert result["theta"] == pytest.approx(-0.09)
    assert result["vega"] == pytest.approx(0.12)
    assert result["rho"] == pytest.approx(0.02)
    assert result["iv"] == pytest.approx(0.25)
    assert result["iv_alpaca"] == pytest.approx(0.26)
