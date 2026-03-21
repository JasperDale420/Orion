from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pandas as pd
import pytest

from orion.connectors import vix_proxy_connector as vpc


@pytest.mark.asyncio
async def test_get_bars_for_symbol_returns_vixy_bars() -> None:
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

    with patch.object(vpc, "get_heber_reader", return_value=_FakeReader()):
        connector = vpc.VIXProxyConnector()
        bars = await connector._get_bars_for_symbol("VIXY")

    assert len(bars) == 2
    assert bars[0]["close"] == 10.0
    assert bars[1]["close"] == 11.0
    assert bars[0]["ts"] < bars[1]["ts"]


@pytest.mark.asyncio
async def test_get_bars_for_symbol_returns_empty_when_heber_unavailable() -> None:
    class _FakeReader:
        def read_bars(self, **_kwargs):
            raise RuntimeError("heber unavailable")

    with patch.object(vpc, "get_heber_reader", return_value=_FakeReader()):
        connector = vpc.VIXProxyConnector()
        bars = await connector._get_bars_for_symbol("VIXY")

    assert bars == []


@pytest.mark.asyncio
async def test_get_bars_for_symbol_uses_default_supported_timeframe() -> None:
    now = datetime.now(UTC)
    captured: dict[str, object] = {}

    class _FakeReader:
        def read_bars(self, **kwargs):
            captured.update(kwargs)
            return pd.DataFrame(
                {
                    "instrument_key": ["equity:VIXY"],
                    "ts_event": [now],
                    "close": [12.0],
                }
            )

    with patch.object(vpc, "get_heber_reader", return_value=_FakeReader()):
        connector = vpc.VIXProxyConnector()
        bars = await connector._get_bars_for_symbol("VIXY")

    assert bars
    assert captured.get("symbols") == ["VIXY"]


@pytest.mark.asyncio
async def test_get_vix_proxy_bars_tries_candidates_in_order() -> None:
    """When VIXY returns no data, falls back to UVIX."""
    now = datetime.now(UTC)
    call_log: list[str] = []

    class _FakeReader:
        def read_bars(self, symbols=None, **_kwargs):
            symbol = symbols[0] if symbols else "UNKNOWN"
            call_log.append(symbol)
            if symbol == "UVIX":
                return pd.DataFrame(
                    {
                        "instrument_key": ["equity:UVIX"],
                        "ts_event": [now],
                        "close": [8.0],
                    }
                )
            return pd.DataFrame()

    with patch.object(vpc, "get_heber_reader", return_value=_FakeReader()):
        connector = vpc.VIXProxyConnector()
        bars, multiplier = await connector._get_vix_proxy_bars()

    assert call_log[0] == "VIXY"
    assert "UVIX" in call_log
    assert len(bars) == 1
    assert bars[0]["close"] == 8.0
    assert bars[0]["symbol"] == "UVIX"
    assert multiplier == 2.85


@pytest.mark.asyncio
async def test_get_bars_for_symbol_filters_stale_data() -> None:
    """Bars older than 10 days are excluded."""
    now = datetime.now(UTC)
    bars_df = pd.DataFrame(
        {
            "instrument_key": ["equity:VIXY"],
            "ts_event": [now - timedelta(days=15)],
            "close": [10.0],
        }
    )

    class _FakeReader:
        def read_bars(self, **_kwargs):
            return bars_df

    with patch.object(vpc, "get_heber_reader", return_value=_FakeReader()):
        connector = vpc.VIXProxyConnector()
        bars = await connector._get_bars_for_symbol("VIXY")

    assert bars == []


@pytest.mark.asyncio
async def test_persist_and_get_current_vix_use_in_memory_cache() -> None:
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
