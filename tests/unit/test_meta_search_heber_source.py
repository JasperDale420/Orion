from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from orion.agents.meta_search_agent import MetaSearchAgent
from orion.core.solver_schema import EvaluationTask


@pytest.mark.asyncio
async def test_fetch_silver_events_prefers_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = MetaSearchAgent.__new__(MetaSearchAgent)
    now = datetime.now(timezone.utc)
    task = EvaluationTask(
        task_id="t1",
        start_time_utc=now - timedelta(hours=1),
        end_time_utc=now,
        ticker_filter=["AAPL"],
    )

    fake_reader = MagicMock()
    fake_reader.read_bars.return_value = pd.DataFrame(
        {
            "symbol": ["AAPL", "AAPL"],
            "bar_start_ts": [now - timedelta(minutes=3), now - timedelta(minutes=2)],
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.0, 100.0],
            "close": [100.5, 101.5],
            "volume": [1000, 1500],
            "vwap": [100.4, 101.4],
            "trade_count": [25, 30],
        }
    )
    fake_reader.read_flow.return_value = pd.DataFrame(
        {
            "event_id": ["e1"],
            "ticker": ["AAPL"],
            "flow_ts_utc": [now - timedelta(minutes=1)],
            "premium_usd": [250000.0],
            "put_call": ["C"],
            "is_sweep": [True],
            "aggressor": ["ASK"],
            "underlying_price": [101.6],
            "expiry": ["2026-03-20"],
        }
    )

    monkeypatch.setattr("orion.agents.meta_search_agent.get_heber_reader", lambda: fake_reader)

    bars, flows, price_data = await agent._fetch_silver_events(task)

    assert len(bars) == 2
    assert len(flows) == 1
    assert "AAPL" in price_data


@pytest.mark.asyncio
async def test_fetch_silver_events_returns_empty_when_heber_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = MetaSearchAgent.__new__(MetaSearchAgent)
    now = datetime.now(timezone.utc)
    task = EvaluationTask(
        task_id="t2",
        start_time_utc=now - timedelta(hours=1),
        end_time_utc=now,
        ticker_filter=["AAPL"],
    )

    fake_reader = MagicMock()
    fake_reader.read_bars.side_effect = RuntimeError("heber unavailable")
    fake_reader.read_flow.side_effect = RuntimeError("heber unavailable")

    monkeypatch.setattr("orion.agents.meta_search_agent.get_heber_reader", lambda: fake_reader)
    assert not hasattr(MetaSearchAgent, "_fetch_events_from_local_sql")

    bars, flows, price_data = await agent._fetch_silver_events(task)

    assert bars == []
    assert flows == []
    assert price_data == {}
