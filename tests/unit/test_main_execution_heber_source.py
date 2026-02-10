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
