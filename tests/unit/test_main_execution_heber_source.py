from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from orion import main_execution


@pytest.mark.asyncio
async def test_fetch_recent_flow_for_ticker_prefers_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
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
async def test_fetch_recent_flow_for_ticker_returns_empty_when_heber_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_reader = MagicMock()
    fake_reader.read_flow.side_effect = RuntimeError("heber unavailable")
    db_calls = {"count": 0}

    async def _db_query(_operation):
        db_calls["count"] += 1
        return []

    monkeypatch.delenv("ORION_EXECUTION_PREFER_HEBER_RECENT_FLOW", raising=False)
    monkeypatch.setattr(main_execution, "get_heber_reader", lambda: fake_reader)
    monkeypatch.setattr(main_execution, "db_query", _db_query)

    rows = await main_execution.fetch_recent_flow_for_ticker("AAPL", minutes=30)

    assert rows == []
    assert db_calls["count"] == 0


def test_prefer_heber_recent_flow_source_env_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_EXECUTION_PREFER_HEBER_RECENT_FLOW", "off")
    assert main_execution._prefer_heber_recent_flow_source() is False


def test_flow_normalizers_handle_alias_values() -> None:
    assert main_execution._normalize_flow_ticker(None) is None
    assert main_execution._normalize_flow_ticker("opra:aapl") == "AAPL"
    assert main_execution._normalize_flow_ticker("   ") is None
    assert main_execution._normalize_put_call(None) == ""
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
    db_calls = {"count": 0}

    async def _db_query(_operation):
        db_calls["count"] += 1
        return []

    monkeypatch.setenv("ORION_EXECUTION_PREFER_HEBER_RECENT_FLOW", "false")
    monkeypatch.setattr(main_execution, "get_heber_reader", lambda: fake_reader)
    monkeypatch.setattr(main_execution, "db_query", _db_query)

    rows = await main_execution.fetch_recent_flow_for_ticker("AAPL", minutes=30)

    assert rows == []
    assert fake_reader.read_flow.call_count == 0
    assert db_calls["count"] == 0


@pytest.mark.asyncio
async def test_fetch_recent_flow_from_heber_skips_invalid_and_non_matching_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    fake_reader = MagicMock()
    fake_reader.read_flow.return_value = pd.DataFrame(
        {
            "ticker": ["MSFT", "AAPL", "AAPL", "AAPL"],
            "flow_ts_utc": [now, now, now, now],
            "premium_usd": [1000.0, "bad", 1200.0, 1400.0],
            "put_call": ["call", "put", "call", "put"],
            "underlying_price": ["bad", 210.0, "bad", 220.0],
            "strike": ["bad", 190.0, "bad", 195.0],
        }
    )
    monkeypatch.setattr(main_execution, "get_heber_reader", lambda: fake_reader)

    rows = await main_execution._fetch_recent_flow_from_heber("AAPL", minutes=30)

    # MSFT row is skipped, invalid premium row is skipped, and two valid AAPL rows survive.
    assert len(rows) == 2
    assert all(r.ticker == "AAPL" for r in rows)
    assert {r.premium_usd for r in rows} == {1200.0, 1400.0}
    # Non-numeric coercions should safely map to None.
    assert any(r.underlying_price is None for r in rows)
    assert any(r.strike is None for r in rows)
