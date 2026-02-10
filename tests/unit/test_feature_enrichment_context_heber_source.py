from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from orion import main_feature_enrichment as feature_enrichment


@pytest.mark.asyncio
async def test_get_latest_market_tide_prefers_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    tide_df = pd.DataFrame(
        {
            "ts_event": [now - timedelta(minutes=3), now - timedelta(minutes=1)],
            "net_call_premium": [200.0, 300.0],
            "net_put_premium": [100.0, 250.0],
        }
    )

    monkeypatch.delenv("ORION_FEATURE_ENRICHMENT_PREFER_HEBER_CONTEXT", raising=False)
    monkeypatch.setattr(feature_enrichment._heber_reader, "read_market_tide", lambda **_kwargs: tide_df)

    async def _fail_db_query(_query_fn):
        raise AssertionError("db_query fallback should not be called")

    monkeypatch.setattr(feature_enrichment, "db_query", _fail_db_query)

    value = await feature_enrichment.get_latest_market_tide()

    assert value == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_get_latest_market_tide_falls_back_to_local_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feature_enrichment._heber_reader,
        "read_market_tide",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("heber unavailable")),
    )

    async def _fake_db_query(_query_fn):
        return 12.5

    monkeypatch.setattr(feature_enrichment, "db_query", _fake_db_query)

    value = await feature_enrichment.get_latest_market_tide()

    assert value == pytest.approx(12.5)


@pytest.mark.asyncio
async def test_get_spy_cumulative_return_prefers_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    bars_df = pd.DataFrame(
        {
            "bar_start_ts": [now - timedelta(minutes=3), now - timedelta(minutes=2), now - timedelta(minutes=1)],
            "close": [100.0, 110.0, 120.0],
            "symbol": ["SPY", "SPY", "SPY"],
        }
    )

    monkeypatch.delenv("ORION_FEATURE_ENRICHMENT_PREFER_HEBER_CONTEXT", raising=False)
    monkeypatch.setattr(feature_enrichment._heber_reader, "read_bars", lambda **_kwargs: bars_df)

    async def _fail_db_query(_query_fn):
        raise AssertionError("db_query fallback should not be called")

    monkeypatch.setattr(feature_enrichment, "db_query", _fail_db_query)

    value = await feature_enrichment.get_spy_cumulative_return()

    assert value == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_get_spy_cumulative_return_falls_back_to_local_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feature_enrichment._heber_reader,
        "read_bars",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("heber unavailable")),
    )

    async def _fake_db_query(_query_fn):
        return 0.15

    monkeypatch.setattr(feature_enrichment, "db_query", _fake_db_query)

    value = await feature_enrichment.get_spy_cumulative_return()

    assert value == pytest.approx(0.15)


@pytest.mark.asyncio
async def test_context_reads_can_disable_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_FEATURE_ENRICHMENT_PREFER_HEBER_CONTEXT", "false")
    db_called = {"value": False}

    monkeypatch.setattr(feature_enrichment._heber_reader, "read_market_tide", lambda **_kwargs: pd.DataFrame())

    async def _fake_db_query(_query_fn):
        db_called["value"] = True
        return 0.0

    monkeypatch.setattr(feature_enrichment, "db_query", _fake_db_query)

    value = await feature_enrichment.get_latest_market_tide()

    assert value == 0.0
    assert db_called["value"] is True
