from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest

from orion.agents.eod_review_agent import EODReviewAgent


@pytest.mark.asyncio
async def test_load_regime_bars_from_heber_returns_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    bars_df = pd.DataFrame(
        {
            "symbol": ["SPY", "SPY", "QQQ"],
            "bar_start_ts": [now - timedelta(minutes=3), now - timedelta(minutes=2), now - timedelta(minutes=1)],
            "close": [500.0, 501.0, 400.0],
        }
    )

    fake_reader = MagicMock()
    fake_reader.read_bars.return_value = bars_df
    monkeypatch.setattr("orion.agents.eod_review_agent.get_heber_reader", lambda: fake_reader)

    agent = EODReviewAgent(vector_store=MagicMock(), proposal_builder=MagicMock())
    rows = await agent._load_regime_bars_from_heber(
        tickers=["SPY"],
        start_ts=now - timedelta(hours=1),
        end_ts=now,
    )

    assert rows is not None
    assert len(rows) == 2
    assert all(getattr(r, "ticker", None) == "SPY" for r in rows)
    assert all(isinstance(getattr(r, "close", None), float) for r in rows)


@pytest.mark.asyncio
async def test_load_regime_bars_from_heber_returns_empty_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_reader = MagicMock()
    fake_reader.read_bars.side_effect = RuntimeError("heber unavailable")
    monkeypatch.setattr("orion.agents.eod_review_agent.get_heber_reader", lambda: fake_reader)

    now = datetime.now(UTC)
    agent = EODReviewAgent(vector_store=MagicMock(), proposal_builder=MagicMock())
    rows = await agent._load_regime_bars_from_heber(
        tickers=["SPY"],
        start_ts=now - timedelta(hours=1),
        end_ts=now,
    )

    assert rows == []


def test_prefer_heber_regime_bars_toggle_is_removed() -> None:
    agent = EODReviewAgent(vector_store=MagicMock(), proposal_builder=MagicMock())
    assert not hasattr(agent, "_prefer_heber_regime_bars")
