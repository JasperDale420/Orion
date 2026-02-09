from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

import orion.ml.flow_enricher as enricher


@pytest.mark.asyncio
async def test_get_flow_greeks_delegates_to_labeler_and_p2_when_option_chain_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 0, tzinfo=timezone.utc)
    captured: dict[str, Any] = {}

    async def _labeler_flow(event_id: str) -> dict[str, float]:
        captured["event_id"] = event_id
        return {
            "delta": 0.51,
            "gamma": 0.03,
            "theta": -0.07,
            "vega": 0.12,
            "iv": 0.28,
            "volume": 88.0,
            "open_interest": 240.0,
        }

    async def _labeler_p2(ticker: str, option_chain: str, ts: datetime) -> dict[str, float]:
        captured["ticker"] = ticker
        captured["option_chain"] = option_chain
        captured["entry_ts"] = ts
        return {
            "iv_vs_hv_ratio": 1.34,
            "oi_change_1d": 45.0,
            "oi_change_pct": 23.7,
        }

    async def _fail_db_query(_query):
        raise AssertionError("local db_query should not be used for flow greeks enrichment")

    monkeypatch.setattr(enricher, "get_labeler_flow_greeks", _labeler_flow, raising=False)
    monkeypatch.setattr(enricher, "get_labeler_p2_features", _labeler_p2, raising=False)
    monkeypatch.setattr(enricher, "db_query", _fail_db_query, raising=False)

    result = await enricher._get_flow_greeks(
        event_id="evt-1",
        ticker="AAPL",
        entry_ts=entry_ts,
        option_chain="AAPL260221C00190000",
    )

    assert result == {
        "delta": 0.51,
        "gamma": 0.03,
        "theta": -0.07,
        "vega": 0.12,
        "iv": 0.28,
        "volume": 88.0,
        "open_interest": 240.0,
        "iv_vs_hv_ratio": 1.34,
        "oi_change_1d": 45.0,
        "oi_change_pct": 23.7,
    }
    assert captured == {
        "event_id": "evt-1",
        "ticker": "AAPL",
        "option_chain": "AAPL260221C00190000",
        "entry_ts": entry_ts,
    }


@pytest.mark.asyncio
async def test_get_flow_greeks_skips_p2_when_option_chain_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _labeler_flow(_event_id: str) -> dict[str, float]:
        return {
            "delta": 0.4,
            "gamma": 0.02,
            "theta": -0.05,
            "vega": 0.09,
            "iv": 0.22,
            "volume": 50.0,
            "open_interest": 120.0,
        }

    async def _fail_p2(*_args, **_kwargs):
        raise AssertionError("P2 helper should not be called when option_chain is missing")

    monkeypatch.setattr(enricher, "get_labeler_flow_greeks", _labeler_flow, raising=False)
    monkeypatch.setattr(enricher, "get_labeler_p2_features", _fail_p2, raising=False)

    result = await enricher._get_flow_greeks(
        event_id="evt-2",
        ticker="AAPL",
        entry_ts=datetime(2026, 2, 11, 15, 0, tzinfo=timezone.utc),
        option_chain=None,
    )

    assert result["delta"] == pytest.approx(0.4)
    assert result["gamma"] == pytest.approx(0.02)
    assert result["theta"] == pytest.approx(-0.05)
    assert result["vega"] == pytest.approx(0.09)
    assert result["iv"] == pytest.approx(0.22)
    assert result["volume"] == pytest.approx(50.0)
    assert result["open_interest"] == pytest.approx(120.0)
    assert result["iv_vs_hv_ratio"] is None
    assert result["oi_change_1d"] is None
    assert result["oi_change_pct"] is None


@pytest.mark.asyncio
async def test_get_market_tide_delegates_to_labeler(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 0, tzinfo=timezone.utc)
    captured: dict[str, Any] = {}

    async def _labeler_tide(ts: datetime, minutes: int = 30) -> dict[str, Any]:
        captured["entry_ts"] = ts
        captured["minutes"] = minutes
        return {"net_premium": 123.0, "direction": "BULLISH"}

    async def _fail_db_query(_query):
        raise AssertionError("local db_query should not be used for market tide enrichment")

    monkeypatch.setattr(enricher, "get_labeler_market_tide_before_entry", _labeler_tide, raising=False)
    monkeypatch.setattr(enricher, "db_query", _fail_db_query, raising=False)

    value = await enricher._get_market_tide(entry_ts, minutes=45)

    assert value == {"net_premium": 123.0, "direction": "BULLISH"}
    assert captured == {"entry_ts": entry_ts, "minutes": 45}


@pytest.mark.asyncio
async def test_get_iv_rank_delegates_to_labeler(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 0, tzinfo=timezone.utc)
    captured: dict[str, Any] = {}

    async def _labeler_iv_rank(ticker: str, ts: datetime) -> float:
        captured["ticker"] = ticker
        captured["entry_ts"] = ts
        return 77.0

    async def _fail_db_query(_query):
        raise AssertionError("local db_query should not be used for iv-rank enrichment")

    monkeypatch.setattr(enricher, "get_labeler_iv_rank_at_entry", _labeler_iv_rank, raising=False)
    monkeypatch.setattr(enricher, "db_query", _fail_db_query, raising=False)

    value = await enricher._get_iv_rank("AAPL", entry_ts)

    assert value == pytest.approx(77.0)
    assert captured == {"ticker": "AAPL", "entry_ts": entry_ts}


@pytest.mark.asyncio
async def test_get_darkpool_volumes_delegates_to_labeler_and_maps_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 0, tzinfo=timezone.utc)
    captured: dict[str, Any] = {}

    async def _labeler_darkpool(ticker: str, ts: datetime) -> dict[str, Any]:
        captured["ticker"] = ticker
        captured["entry_ts"] = ts
        return {
            "darkpool_30m": 10.0,
            "darkpool_1h": 20.0,
            "darkpool_4h": 40.0,
            "darkpool_1d": 100.0,
            "darkpool_1w": 700.0,
        }

    async def _fail_db_query(_query):
        raise AssertionError("local db_query should not be used for darkpool enrichment")

    monkeypatch.setattr(enricher, "get_labeler_darkpool_metrics", _labeler_darkpool, raising=False)
    monkeypatch.setattr(enricher, "db_query", _fail_db_query, raising=False)

    value = await enricher._get_darkpool_volumes("AAPL", entry_ts)

    assert value == {
        "30m": 10.0,
        "1h": 20.0,
        "4h": 40.0,
        "1d": 100.0,
    }
    assert captured == {"ticker": "AAPL", "entry_ts": entry_ts}


@pytest.mark.asyncio
async def test_get_regime_delegates_to_labeler(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 11, 15, 0, tzinfo=timezone.utc)
    captured: dict[str, Any] = {}

    async def _labeler_regime(ts: datetime) -> dict[str, Any]:
        captured["entry_ts"] = ts
        return {
            "trend_regime": "UP",
            "vol_regime": "MEDIUM",
            "risk_regime": "ON",
            "session_regime": "MID",
            "vix_regime": "NORMAL",
        }

    async def _fail_db_query(_query):
        raise AssertionError("local db_query should not be used for regime enrichment")

    monkeypatch.setattr(enricher, "get_labeler_regime_at_entry", _labeler_regime, raising=False)
    monkeypatch.setattr(enricher, "db_query", _fail_db_query, raising=False)

    value = await enricher._get_regime(entry_ts)

    assert value == {
        "trend_regime": "UP",
        "vol_regime": "MEDIUM",
        "risk_regime": "ON",
        "session_regime": "MID",
        "vix_regime": "NORMAL",
    }
    assert captured == {"entry_ts": entry_ts}
