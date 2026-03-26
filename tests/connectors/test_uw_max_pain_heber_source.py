from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from orion.connectors import uw_max_pain_connector as max_pain


@pytest.mark.asyncio
async def test_get_current_price_prefers_heber_without_local_db_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    bars_df = pd.DataFrame(
        {
            "instrument_key": ["equity:AAPL", "equity:MSFT", "equity:AAPL"],
            "ts_event": [now - timedelta(minutes=5), now - timedelta(minutes=3), now - timedelta(minutes=1)],
            "close": [199.5, 450.0, 200.25],
        }
    )

    class _FakeReader:
        def read_bars(self, **_kwargs):
            return bars_df

    monkeypatch.setattr(max_pain, "get_heber_reader", lambda: _FakeReader())

    connector = max_pain.UWMaxPainConnector(gateway_url="http://gateway:8080", gateway_key="test")
    price = await connector._get_current_price("AAPL")

    assert price == pytest.approx(200.25)


@pytest.mark.asyncio
async def test_get_current_price_returns_none_when_heber_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeReader:
        def read_bars(self, **_kwargs):
            raise RuntimeError("heber unavailable")

    monkeypatch.setattr(max_pain, "get_heber_reader", lambda: _FakeReader())

    connector = max_pain.UWMaxPainConnector(gateway_url="http://gateway:8080", gateway_key="test")
    price = await connector._get_current_price("AAPL")

    assert price is None
