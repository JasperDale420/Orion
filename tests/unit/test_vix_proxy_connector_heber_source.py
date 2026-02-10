from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from orion.connectors import vix_proxy_connector as vpc


@pytest.mark.asyncio
async def test_get_vixy_bars_prefers_heber_without_local_db_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
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

    monkeypatch.setattr(vpc, "db_query", _fail_db_query)

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

    monkeypatch.setattr(vpc, "db_query", _fail_db_query)

    connector = vpc.VIXProxyConnector()
    bars = await connector._get_vixy_bars()

    assert bars == []
