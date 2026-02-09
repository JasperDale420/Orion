from __future__ import annotations

from datetime import datetime, timezone

import pytest

import orion.jobs.backfill_ml_features as backfill
import orion.main_price_target_labeler as labeler


def _async_return(value):
    async def _inner(*_args, **_kwargs):
        return value

    return _inner


@pytest.mark.asyncio
async def test_update_ml_features_calls_sector_corr_with_two_args(monkeypatch: pytest.MonkeyPatch) -> None:
    record = {
        "event_id": "evt-1",
        "ticker": "AAPL",
        "entry_ts": datetime(2026, 2, 6, 16, 0, tzinfo=timezone.utc),
        "expiry": "2026-02-21",
        "dte": 15,
        "option_chain": "AAPL260221C00100000",
    }

    # Local backfill helpers
    monkeypatch.setattr(backfill, "get_flow_greeks", _async_return({"delta": 0.1, "gamma": 0.01, "volume": 10, "open_interest": 50, "iv": 0.4}))
    monkeypatch.setattr(backfill, "get_underlying_price_at_entry", _async_return(100.0))
    monkeypatch.setattr(backfill, "get_underlying_price_at_offset", _async_return(101.0))
    monkeypatch.setattr(backfill, "get_ticker_info", _async_return({"sector": "Technology", "next_earnings_date": None}))
    monkeypatch.setattr(backfill, "get_earnings_proximity", _async_return({"days_to_earnings": None, "is_post_earnings": None}))
    monkeypatch.setattr(backfill, "get_gex_at_entry", _async_return({"gex": 1.0, "vex": 2.0}))
    monkeypatch.setattr(backfill, "get_max_pain_distance", _async_return(0.5))
    monkeypatch.setattr(backfill, "db_write", _async_return(None))

    monkeypatch.setattr(backfill, "get_phase1_bucket_features", _async_return({"overnight_gap_pct": 0.1, "vwap_distance_pct": 0.2, "minutes_to_close": 60, "price_change_5d_prior": 1.2, "earnings_in_dte_window": False}))

    # Labeler helpers imported inside update_ml_features
    monkeypatch.setattr(labeler, "get_darkpool_metrics", _async_return({"darkpool_1h": 1, "darkpool_15m": 1, "darkpool_30m": 1, "darkpool_4h": 1, "darkpool_1d": 1, "darkpool_3d": 1, "darkpool_1w": 1, "darkpool_2w": 1, "darkpool_4w": 1}))
    monkeypatch.setattr(
        labeler,
        "get_rvol_metrics",
        _async_return(
            {
                "rvol_1h": 1.0,
                "rvol_daily": 1.0,
                "rvol_weekly": 1.0,
                "rvol_30m": 1.0,
                "rvol_3d": 1.0,
                "rvol_monthly": 1.0,
            }
        ),
    )
    monkeypatch.setattr(labeler, "get_flow_aggression", _async_return({"ask_side_ratio": 0.7, "sweep_ratio_1h": 0.2, "same_ticker_premium_1h": 10000}))
    monkeypatch.setattr(labeler, "get_institutional_flow_1w", _async_return(100000))
    monkeypatch.setattr(labeler, "get_market_tide_before_entry", _async_return({"net_premium": 123.0, "direction": "BULLISH"}))
    monkeypatch.setattr(labeler, "get_regime_at_entry", _async_return({"trend_regime": "UP", "vol_regime": "NORMAL", "risk_regime": "ON", "session_regime": "MID", "vix_at_entry": 18.0, "vix_regime": "NORMAL"}))
    monkeypatch.setattr(labeler, "get_p2_features", _async_return({"oi_change_1d": 1.0, "oi_change_pct": 2.0, "iv_vs_hv_ratio": 1.1}))
    monkeypatch.setattr(labeler, "get_p3_features", _async_return({"high_52w_distance_pct": 3.0, "is_spread_leg": False, "same_expiry_trades_1h": 1}))
    monkeypatch.setattr(labeler, "get_iv_rank_at_entry", _async_return(55.0))

    captured: dict[str, object] = {}

    async def _sector_corr(ticker: str, entry_ts: datetime) -> dict[str, object]:
        captured["ticker"] = ticker
        captured["entry_ts"] = entry_ts
        return {
            "sector_net_premium_1h": 10.0,
            "sector_flow_direction": "BULLISH",
            "spy_correlation_5d": 0.5,
            "spy_return_1h": 0.2,
        }

    # Regression guard: this stub accepts exactly 2 args.
    monkeypatch.setattr(labeler, "get_sector_correlation_features", _sector_corr)

    ok = await backfill.update_ml_features(record)

    assert ok is True
    assert captured["ticker"] == "AAPL"
    assert captured["entry_ts"] == record["entry_ts"]


@pytest.mark.asyncio
async def test_get_underlying_price_at_entry_delegates_to_labeler(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    captured: dict[str, object] = {}

    async def _labeler_entry(ticker: str, ts: datetime) -> float:
        captured["ticker"] = ticker
        captured["entry_ts"] = ts
        return 123.45

    async def _fail_db_query(_callback):
        raise AssertionError("local db_query should not be used for underlying entry lookup")

    monkeypatch.setattr(backfill, "get_labeler_underlying_price_at_entry", _labeler_entry, raising=False)
    monkeypatch.setattr(backfill, "db_query", _fail_db_query, raising=False)

    value = await backfill.get_underlying_price_at_entry("AAPL", entry_ts)

    assert value == 123.45
    assert captured == {"ticker": "AAPL", "entry_ts": entry_ts}


@pytest.mark.asyncio
async def test_get_underlying_price_at_offset_delegates_to_labeler(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    captured: dict[str, object] = {}

    async def _labeler_offset(ticker: str, ts: datetime, hours: int) -> float:
        captured["ticker"] = ticker
        captured["entry_ts"] = ts
        captured["hours"] = hours
        return 124.0

    async def _fail_db_query(_callback):
        raise AssertionError("local db_query should not be used for underlying offset lookup")

    monkeypatch.setattr(backfill, "get_labeler_underlying_price_at_offset", _labeler_offset, raising=False)
    monkeypatch.setattr(backfill, "db_query", _fail_db_query, raising=False)

    value = await backfill.get_underlying_price_at_offset("AAPL", entry_ts, hours=2)

    assert value == 124.0
    assert captured == {"ticker": "AAPL", "entry_ts": entry_ts, "hours": 2}


@pytest.mark.asyncio
async def test_get_flow_greeks_delegates_to_labeler(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def _labeler_flow_greeks(event_id: str) -> dict[str, object]:
        captured["event_id"] = event_id
        return {"delta": 0.2, "gamma": 0.03, "volume": 11, "open_interest": 70, "iv": 0.5}

    async def _fail_db_query(_callback):
        raise AssertionError("local db_query should not be used for flow-greeks lookup")

    monkeypatch.setattr(backfill, "get_labeler_flow_greeks", _labeler_flow_greeks, raising=False)
    monkeypatch.setattr(backfill, "db_query", _fail_db_query, raising=False)

    value = await backfill.get_flow_greeks("evt-123")

    assert value == {"delta": 0.2, "gamma": 0.03, "volume": 11, "open_interest": 70, "iv": 0.5}
    assert captured == {"event_id": "evt-123"}


@pytest.mark.asyncio
async def test_get_ticker_info_delegates_to_labeler(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    expected = {
        "sector": "Technology",
        "next_earnings_date": None,
        "announce_time": None,
        "last_earnings_date": None,
    }

    async def _labeler_ticker_info(ticker: str) -> dict[str, object]:
        captured["ticker"] = ticker
        return expected

    monkeypatch.setattr(backfill, "get_labeler_ticker_info", _labeler_ticker_info, raising=False)

    value = await backfill.get_ticker_info("AAPL")

    assert value == expected
    assert captured == {"ticker": "AAPL"}


@pytest.mark.asyncio
async def test_get_earnings_proximity_delegates_to_labeler(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    captured: dict[str, object] = {}
    expected = {"days_to_earnings": 4, "is_post_earnings": False}

    async def _labeler_earnings(ticker: str, ts: datetime) -> dict[str, object]:
        captured["ticker"] = ticker
        captured["entry_ts"] = ts
        return expected

    monkeypatch.setattr(backfill, "get_labeler_earnings_proximity", _labeler_earnings, raising=False)

    value = await backfill.get_earnings_proximity("AAPL", entry_ts)

    assert value == expected
    assert captured == {"ticker": "AAPL", "entry_ts": entry_ts}


@pytest.mark.asyncio
async def test_get_phase1_bucket_features_delegates_to_labeler(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    captured: dict[str, object] = {}
    expected = {
        "overnight_gap_pct": 0.1,
        "vwap_distance_pct": 0.2,
        "minutes_to_close": 60,
        "price_change_5d_prior": 1.2,
        "earnings_in_dte_window": False,
    }

    async def _labeler_phase1(ticker: str, ts: datetime, dte: int) -> dict[str, object]:
        captured["ticker"] = ticker
        captured["entry_ts"] = ts
        captured["dte"] = dte
        return expected

    monkeypatch.setattr(backfill, "get_labeler_phase1_bucket_features", _labeler_phase1, raising=False)

    value = await backfill.get_phase1_bucket_features("AAPL", entry_ts, 15)

    assert value == expected
    assert captured == {"ticker": "AAPL", "entry_ts": entry_ts, "dte": 15}
