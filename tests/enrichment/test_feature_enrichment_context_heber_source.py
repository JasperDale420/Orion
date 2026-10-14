from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from orion import main_feature_enrichment as feature_enrichment
from orion.config import system_settings
from orion.enrichment import heber_context


@pytest.mark.asyncio
async def test_get_latest_market_tide_prefers_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    tide_df = pd.DataFrame(
        {
            "ts_event": [now - timedelta(minutes=3), now - timedelta(minutes=1)],
            "net_call_premium": [200.0, 300.0],
            "net_put_premium": [100.0, 250.0],
        }
    )

    monkeypatch.setattr(system_settings, "feature_enrichment_prefer_heber_context", True)
    monkeypatch.setattr(heber_context._heber_reader, "read_market_tide", lambda **_kwargs: tide_df)

    async def _fail_db_query(_query_fn):
        raise AssertionError("db_query fallback should not be called")

    monkeypatch.setattr(feature_enrichment, "db_query", _fail_db_query, raising=False)

    value = await feature_enrichment.get_latest_market_tide()

    assert value == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_get_latest_vix_data_prefers_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    bars_df = pd.DataFrame(
        {
            "bar_start_ts": [
                now - timedelta(days=1, minutes=1),
                now - timedelta(days=1),
                now - timedelta(minutes=1),
            ],
            "close": [10.0, 10.0, 12.0],
            "symbol": ["VIXY", "VIXY", "VIXY"],
        }
    )

    monkeypatch.setattr(system_settings, "feature_enrichment_prefer_heber_context", True)
    monkeypatch.setattr(heber_context._heber_reader, "read_bars", lambda **_kwargs: bars_df)

    async def _fail_db_query(_query_fn):
        raise AssertionError("db_query fallback should not be called")

    monkeypatch.setattr(feature_enrichment, "db_query", _fail_db_query, raising=False)

    vix_data = await feature_enrichment.get_latest_vix_data()

    assert vix_data["vix"] == pytest.approx(24.0)
    assert vix_data["vix_1d_change"] == pytest.approx(20.0)
    assert vix_data["vix_regime"] == "ELEVATED"
    # This is an ETF-price proxy, not a real spot-VIX print — downstream
    # (RegimeGate) must be able to tell the two apart before hard-blocking
    # trading on it. See 2026-08-18 adversarial review finding.
    assert vix_data["vix_source"] == "proxy:VIXY"
    assert vix_data["vix_observed_at"] == now - timedelta(minutes=1)


@pytest.mark.asyncio
async def test_get_latest_market_tide_returns_none_when_heber_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        heber_context._heber_reader,
        "read_market_tide",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("heber unavailable")),
    )

    async def _fail_db_query(_query_fn):
        raise AssertionError("db_query fallback should not be called")

    monkeypatch.setattr(feature_enrichment, "db_query", _fail_db_query, raising=False)

    value = await feature_enrichment.get_latest_market_tide()

    assert value is None


@pytest.mark.asyncio
async def test_get_latest_vix_data_returns_empty_when_heber_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        heber_context._heber_reader,
        "read_bars",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("heber unavailable")),
    )

    async def _fail_db_query(_query_fn):
        raise AssertionError("db_query fallback should not be called")

    monkeypatch.setattr(feature_enrichment, "db_query", _fail_db_query, raising=False)

    vix_data = await feature_enrichment.get_latest_vix_data()

    assert vix_data == {}


@pytest.mark.asyncio
async def test_get_spy_cumulative_return_prefers_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    bars_df = pd.DataFrame(
        {
            "bar_start_ts": [now - timedelta(minutes=3), now - timedelta(minutes=2), now - timedelta(minutes=1)],
            "close": [100.0, 110.0, 120.0],
            "symbol": ["SPY", "SPY", "SPY"],
        }
    )

    monkeypatch.setattr(system_settings, "feature_enrichment_prefer_heber_context", True)
    monkeypatch.setattr(heber_context._heber_reader, "read_bars", lambda **_kwargs: bars_df)

    async def _fail_db_query(_query_fn):
        raise AssertionError("db_query fallback should not be called")

    monkeypatch.setattr(feature_enrichment, "db_query", _fail_db_query, raising=False)

    value = await feature_enrichment.get_spy_cumulative_return()

    assert value == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_get_spy_cumulative_return_returns_zero_when_heber_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        heber_context._heber_reader,
        "read_bars",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("heber unavailable")),
    )

    async def _fail_db_query(_query_fn):
        raise AssertionError("db_query fallback should not be called")

    monkeypatch.setattr(feature_enrichment, "db_query", _fail_db_query, raising=False)

    value = await feature_enrichment.get_spy_cumulative_return()

    assert value == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_context_reads_can_disable_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system_settings, "feature_enrichment_prefer_heber_context", False)
    db_called = {"value": False}
    heber_called = {"value": False}

    def _heber_market_tide(**_kwargs):
        heber_called["value"] = True
        return pd.DataFrame()

    async def _fail_db_query(_query_fn):
        db_called["value"] = True
        raise AssertionError("db_query fallback should not be called")

    monkeypatch.setattr(heber_context._heber_reader, "read_market_tide", _heber_market_tide)
    monkeypatch.setattr(feature_enrichment, "db_query", _fail_db_query, raising=False)

    value = await feature_enrichment.get_latest_market_tide()

    assert value is None
    assert db_called["value"] is False
    assert heber_called["value"] is False
