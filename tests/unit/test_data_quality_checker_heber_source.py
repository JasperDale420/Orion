from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from orion.jobs import data_quality_checker as dqc


@pytest.mark.asyncio
async def test_get_flow_summary_prefers_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
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

    monkeypatch.setattr(dqc, "db_query", _fail_db_query)

    summary = await dqc.get_flow_summary()

    assert summary["backend"] == "heber"
    assert summary["total_flows_24h"] == 3
    assert summary["valid_premium"] == 2
    assert summary["missing_premium"] == 1
    assert summary["unique_tickers"] == 2


@pytest.mark.asyncio
async def test_get_flow_summary_falls_back_to_local_db(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeReader:
        def read_flow(self, **_kwargs):
            raise RuntimeError("heber unavailable")

    monkeypatch.setattr(dqc, "get_heber_reader", lambda: _FakeReader())
    local_called = {"value": False}

    async def _fake_db_query(_fn):
        local_called["value"] = True
        return {
            "total_flows_24h": 10,
            "valid_premium": 8,
            "missing_premium": 2,
            "unique_tickers": 4,
            "latest_flow": None,
            "validity_pct": 80.0,
            "backend": "local_db",
        }

    monkeypatch.setattr(dqc, "db_query", _fake_db_query)

    summary = await dqc.get_flow_summary()

    assert local_called["value"] is True
    assert summary["backend"] == "local_db"
    assert summary["total_flows_24h"] == 10


@pytest.mark.asyncio
async def test_check_flow_staleness_uses_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
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

    monkeypatch.setattr(dqc, "get_heber_reader", lambda: _FakeReader())
    monkeypatch.setattr(dqc, "MARKET_OPEN_HOUR", 0)
    monkeypatch.setattr(dqc, "MARKET_CLOSE_HOUR", 24)

    stale = await dqc.check_flow_staleness(stale_minutes=30)
    assert stale is True


@pytest.mark.asyncio
async def test_get_darkpool_summary_prefers_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
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

    monkeypatch.setattr(dqc, "db_query", _fail_db_query)

    summary = await dqc.get_darkpool_summary()

    assert summary["backend"] == "heber"
    assert summary["total_trades_24h"] == 2
    assert summary["valid_trades"] == 1
    assert summary["invalid_price"] == 1
    assert summary["unique_tickers"] == 2
