from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from orion import main_execution


@pytest.mark.asyncio
async def test_fetch_recent_flow_for_ticker_prefers_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    fake_reader = MagicMock()
    fake_reader.read_flow.return_value = pd.DataFrame(
        {
            "ticker": ["AAPL", "AAPL"],
            "flow_ts_utc": [now - timedelta(minutes=2), now - timedelta(minutes=1)],
            "premium_usd": [150000.0, 125000.0],
            "put_call": ["C", "P"],
            "aggressor": ["ASK", "BID"],
            "is_sweep": [True, False],
            "option_chain": ["AAPL260320C00200000", "AAPL260320P00200000"],
        }
    )
    mock_db_query = AsyncMock(return_value=[])

    monkeypatch.delenv("ORION_EXECUTION_PREFER_HEBER_RECENT_FLOW", raising=False)
    monkeypatch.setattr(main_execution, "get_heber_reader", lambda: fake_reader)
    monkeypatch.setattr(main_execution, "db_query", mock_db_query)

    rows = await main_execution.fetch_recent_flow_for_ticker("AAPL", minutes=30)

    assert len(rows) == 2
    assert all(row.ticker == "AAPL" for row in rows)
    assert {row.premium_usd for row in rows} == {150000.0, 125000.0}
    assert {row.put_call for row in rows} == {"C", "P"}
    assert mock_db_query.await_count == 0


@pytest.mark.asyncio
async def test_fetch_recent_flow_for_ticker_falls_back_to_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_reader = MagicMock()
    fake_reader.read_flow.side_effect = RuntimeError("heber unavailable")
    db_rows = [SimpleNamespace(ticker="AAPL", premium_usd=50000.0)]
    mock_db_query = AsyncMock(return_value=db_rows)

    monkeypatch.delenv("ORION_EXECUTION_PREFER_HEBER_RECENT_FLOW", raising=False)
    monkeypatch.setattr(main_execution, "get_heber_reader", lambda: fake_reader)
    monkeypatch.setattr(main_execution, "db_query", mock_db_query)

    rows = await main_execution.fetch_recent_flow_for_ticker("AAPL", minutes=30)

    assert rows == db_rows
    assert mock_db_query.await_count == 1


def test_prefer_heber_recent_flow_source_env_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_EXECUTION_PREFER_HEBER_RECENT_FLOW", "off")
    assert main_execution._prefer_heber_recent_flow_source() is False


def test_flow_normalizers_handle_alias_values() -> None:
    assert main_execution._normalize_flow_ticker("opra:aapl") == "AAPL"
    assert main_execution._normalize_flow_ticker("   ") is None
    assert main_execution._normalize_put_call("call") == "C"
    assert main_execution._normalize_put_call("put") == "P"
    assert main_execution._normalize_put_call("x") == "X"
    assert main_execution._coerce_bool("yes") is True
    assert main_execution._coerce_bool("no") is False
    assert main_execution._coerce_bool(1) is True
    assert main_execution._coerce_bool(None) is False


@pytest.mark.asyncio
async def test_fetch_recent_flow_from_heber_returns_empty_on_missing_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_reader = MagicMock()
    fake_reader.read_flow.return_value = pd.DataFrame({"ticker": ["AAPL"]})
    monkeypatch.setattr(main_execution, "get_heber_reader", lambda: fake_reader)

    rows = await main_execution._fetch_recent_flow_from_heber("AAPL", minutes=30)

    assert rows == []


@pytest.mark.asyncio
async def test_fetch_recent_flow_for_ticker_skips_heber_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_reader = MagicMock()
    db_rows = [SimpleNamespace(ticker="AAPL", premium_usd=42000.0)]
    mock_db_query = AsyncMock(return_value=db_rows)

    monkeypatch.setenv("ORION_EXECUTION_PREFER_HEBER_RECENT_FLOW", "false")
    monkeypatch.setattr(main_execution, "get_heber_reader", lambda: fake_reader)
    monkeypatch.setattr(main_execution, "db_query", mock_db_query)

    rows = await main_execution.fetch_recent_flow_for_ticker("AAPL", minutes=30)

    assert rows == db_rows
    assert fake_reader.read_flow.call_count == 0
