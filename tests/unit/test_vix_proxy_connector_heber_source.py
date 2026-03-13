from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from orion.connectors import vix_proxy_connector as vpc


@pytest.mark.asyncio
async def test_get_vixy_bars_prefers_heber_without_local_db_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    bars_df = pd.DataFrame(
        {
            "instrument_key": ["equity:VIXY", "equity:QQQ", "equity:VIXY"],
            "ts_event": [now - timedelta(days=2), now - timedelta(days=1), now],
            "close": [10.0, 500.0, 11.0],
        }
    )

    class _FakeReader:
        def read_bars(self, **_kwargs):
            return bars_df

    monkeypatch.setattr(vpc, "get_heber_reader", lambda: _FakeReader())

    async def _fail_db_query(_fn):
        raise AssertionError("local bars fallback should not be called")

    monkeypatch.setattr(vpc, "db_query", _fail_db_query, raising=False)

    connector = vpc.VIXProxyConnector()
    bars = await connector._get_vixy_bars()

    assert len(bars) == 2
    assert bars[0]["close"] == 10.0
    assert bars[1]["close"] == 11.0
    assert bars[0]["ts"] < bars[1]["ts"]


@pytest.mark.asyncio
async def test_get_vixy_bars_returns_empty_when_heber_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeReader:
        def read_bars(self, **_kwargs):
            raise RuntimeError("heber unavailable")

    monkeypatch.setattr(vpc, "get_heber_reader", lambda: _FakeReader())

    async def _fail_db_query(_fn):
        raise AssertionError("local bars fallback should not be called")

    monkeypatch.setattr(vpc, "db_query", _fail_db_query, raising=False)

    connector = vpc.VIXProxyConnector()
    bars = await connector._get_vixy_bars()

    assert bars == []


@pytest.mark.asyncio
async def test_get_vixy_bars_uses_default_supported_timeframe(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    captured: dict[str, object] = {}

    class _FakeReader:
        def read_bars(self, **kwargs):
            captured["timeframe"] = kwargs.get("timeframe")
            return pd.DataFrame(
                {
                    "instrument_key": ["equity:VIXY"],
                    "ts_event": [now],
                    "close": [12.0],
                }
            )

    monkeypatch.setattr(vpc, "get_heber_reader", lambda: _FakeReader())

    connector = vpc.VIXProxyConnector()
    bars = await connector._get_vixy_bars()

    assert bars
    assert captured["timeframe"] in (None, "1m")


@pytest.mark.asyncio
async def test_persist_and_get_current_vix_use_in_memory_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail_db_query(_fn):
        raise AssertionError("local db_query should not be used")

    async def _fail_db_write(_fn):
        raise AssertionError("local db_write should not be used")

    monkeypatch.setattr(vpc, "db_query", _fail_db_query, raising=False)
    monkeypatch.setattr(vpc, "db_write", _fail_db_write, raising=False)

    connector = vpc.VIXProxyConnector()
    await connector._persist(
        {
            "ts_utc": datetime.now(UTC),
            "vix": 18.5,
            "vvix": None,
            "vix_1d_change": -1.2,
            "vix_5d_ma": 19.0,
            "vix_regime": "NORMAL",
        }
    )

    current = await connector.get_current_vix()
    assert current == {"vix": 18.5, "vix_1d_change": -1.2, "vix_regime": "NORMAL"}
