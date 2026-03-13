from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from orion.jobs.window_feature_job import WindowFeatureJob


@pytest.mark.asyncio
async def test_build_features_prefers_heber_without_local_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 2, 10, 15, 0, tzinfo=UTC)
    window_start = now - timedelta(minutes=5)

    flow_df = pd.DataFrame(
        {
            "instrument_key": ["equity:SPY", "equity:SPY", "equity:SPY"],
            "put_call": ["C", "P", "C"],
            "premium_usd": [100.0, 40.0, 50.0],
            "is_sweep": [True, False, "true"],
            "aggressor": ["ASK", "BID", "ASK"],
            "iv": [0.25, 0.35, 0.30],
        }
    )
    darkpool_df = pd.DataFrame(
        {
            "instrument_key": ["equity:SPY", "equity:SPY"],
            "size_shares": [1000, 500],
            "trade_price": [10.0, 20.0],
        }
    )

    class _FakeReader:
        def read_flow(self, **_kwargs):
            return flow_df

        def read_darkpool(self, **_kwargs):
            return darkpool_df

    monkeypatch.setattr("orion.jobs.window_feature_job.get_heber_reader", lambda: _FakeReader())

    async def _fail_local(*_args, **_kwargs):
        raise AssertionError("local fallback should not be called")

    job = WindowFeatureJob(tickers=["SPY"], periods=["5m"], prefer_heber=True)
    monkeypatch.setattr(job, "_build_features_from_local_db", _fail_local)

    features = await job._build_features(
        ticker="SPY",
        window_start=window_start,
        window_end=now,
        period="5m",
    )

    assert features is not None
    assert features["flow_count"] == 3
    assert features["sweep_count"] == 2
    assert features["call_premium"] == 150.0
    assert features["put_premium"] == 40.0
    assert features["dp_count"] == 2
    assert features["dp_volume"] == 1500.0


@pytest.mark.asyncio
async def test_build_features_returns_none_when_heber_empty_without_local_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 2, 10, 15, 0, tzinfo=UTC)
    window_start = now - timedelta(minutes=5)

    class _FakeReader:
        def read_flow(self, **_kwargs):
            return pd.DataFrame()

        def read_darkpool(self, **_kwargs):
            return pd.DataFrame()

    monkeypatch.setattr("orion.jobs.window_feature_job.get_heber_reader", lambda: _FakeReader())

    async def _fail_local(*_args, **_kwargs):
        raise AssertionError("local fallback should not be called")

    job = WindowFeatureJob(tickers=["SPY"], periods=["5m"], prefer_heber=True)
    monkeypatch.setattr(job, "_build_features_from_local_db", _fail_local)

    features = await job._build_features(
        ticker="SPY",
        window_start=window_start,
        window_end=now,
        period="5m",
    )

    assert features is None
