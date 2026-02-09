from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

import orion.main_price_target_labeler as labeler


def test_heber_market_tide_net_prefers_pre_aggregated_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "flow_ts_utc": entry_ts - timedelta(minutes=10),
                        "net_call_premium": 120.0,
                        "net_put_premium": -20.0,
                    },
                    {
                        "flow_ts_utc": entry_ts - timedelta(minutes=5),
                        "net_call_premium": 80.0,
                        "net_put_premium": -40.0,
                    },
                ]
            )

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    net = labeler._get_heber_market_tide_net_premium(entry_ts, minutes=30)
    assert net == 140.0


def test_heber_market_tide_net_derives_from_put_call_when_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {"flow_ts_utc": entry_ts - timedelta(minutes=15), "put_call": "CALL", "premium_usd": 200.0},
                    {"flow_ts_utc": entry_ts - timedelta(minutes=5), "put_call": "PUT", "premium_usd": 70.0},
                ]
            )

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    net = labeler._get_heber_market_tide_net_premium(entry_ts, minutes=30)
    assert net == 130.0


@pytest.mark.asyncio
async def test_get_market_tide_before_entry_prefers_heber_net(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    def _fake_heber_net(_entry_ts: datetime, minutes: int = 30) -> float:
        assert minutes == 45
        return 250.0

    async def _fail_db_query(_callback):
        raise AssertionError("db_query should not be used when Heber market tide net is available")

    monkeypatch.setattr(labeler, "_get_heber_market_tide_net_premium", _fake_heber_net, raising=False)
    monkeypatch.setattr(labeler, "db_query", _fail_db_query, raising=False)

    result = await labeler.get_market_tide_before_entry(entry_ts, minutes=45)
    assert result == {"net_premium": 250.0, "direction": "BULLISH"}


@pytest.mark.asyncio
async def test_get_regime_at_entry_uses_heber_tide_before_sql_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import orion.analysis.regime as regime_module

    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    captured: dict[str, Any] = {"calls": []}

    class _FakeDetector:
        def detect(self, **kwargs: Any):
            captured["market_tide_net"] = kwargs.get("market_tide_net")
            return SimpleNamespace(
                trend=SimpleNamespace(value="UP"),
                vol=SimpleNamespace(value="NORMAL"),
                risk=SimpleNamespace(value="RISK_ON"),
                session=SimpleNamespace(value="OPEN"),
                vix_level=19.5,
                vix_regime=SimpleNamespace(value="ELEVATED"),
            )

    async def _fake_db_query(callback):
        captured["calls"].append(callback.__name__)
        assert callback.__name__ == "query_vix"
        return {"vix": 19.5, "vix_1d_change": 0.8, "vix_regime": "ELEVATED"}

    monkeypatch.setattr(regime_module, "MultiAxisRegimeDetector", _FakeDetector)
    monkeypatch.setattr(labeler, "_get_heber_market_tide_net_premium", lambda *_args, **_kwargs: 77.0, raising=False)
    monkeypatch.setattr(labeler, "db_query", _fake_db_query, raising=False)

    result = await labeler.get_regime_at_entry(entry_ts)

    assert captured["calls"] == ["query_vix"]
    assert captured["market_tide_net"] == 77.0
    assert result["vix_at_entry"] == 19.5
