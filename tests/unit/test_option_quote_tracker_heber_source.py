from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from orion import main_option_quote_tracker as oqt


@pytest.mark.asyncio
async def test_get_pending_checkpoints_prefers_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    flow_df = pd.DataFrame(
        {
            "event_id": ["evt-1", "evt-2"],
            "symbol": ["SPY", "QQQ"],
            "expiry": ["2026-02-20", None],
            "put_call": ["C", "P"],
            "strike": [500.0, 450.0],
            "ts_event": [now - timedelta(minutes=12), now - timedelta(minutes=4)],
        }
    )

    class _FakeReader:
        def read_flow(self, **_kwargs):
            return flow_df

    monkeypatch.delenv("ORION_OPTION_QUOTE_TRACKER_PREFER_HEBER", raising=False)
    monkeypatch.setattr(oqt, "get_heber_reader", lambda: _FakeReader())

    async def _fail_db_query(_fn):
        raise AssertionError("local fallback should not be called")

    monkeypatch.setattr(oqt, "db_query", _fail_db_query)

    pending = await oqt.get_pending_checkpoints()

    assert len(pending) == 1
    assert pending[0]["event_id"] == "evt-1"
    assert pending[0]["ticker"] == "SPY"
    assert pending[0]["option_symbol"] == "SPY260220C00500000"
    assert pending[0]["minutes_since_entry"] >= 0


@pytest.mark.asyncio
async def test_get_pending_checkpoints_returns_empty_when_heber_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeReader:
        def read_flow(self, **_kwargs):
            raise RuntimeError("heber unavailable")

    monkeypatch.setattr(oqt, "get_heber_reader", lambda: _FakeReader())

    async def _fail_db_query(_fn):
        raise AssertionError("local fallback should not be called")

    monkeypatch.setattr(oqt, "db_query", _fail_db_query)

    pending = await oqt.get_pending_checkpoints()

    assert pending == []


@pytest.mark.asyncio
async def test_get_pending_checkpoints_can_disable_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_OPTION_QUOTE_TRACKER_PREFER_HEBER", "false")

    async def _fail_db_query(_fn):
        raise AssertionError("local fallback should not be called")

    monkeypatch.setattr(oqt, "db_query", _fail_db_query)

    pending = await oqt.get_pending_checkpoints()

    assert pending == []
