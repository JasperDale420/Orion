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
