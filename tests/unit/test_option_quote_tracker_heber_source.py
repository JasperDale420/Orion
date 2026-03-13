from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from orion import main_option_quote_tracker as oqt


@pytest.mark.asyncio
async def test_get_pending_checkpoints_prefers_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
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

    monkeypatch.setattr(oqt, "db_query", _fail_db_query, raising=False)

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

    monkeypatch.setattr(oqt, "db_query", _fail_db_query, raising=False)

    pending = await oqt.get_pending_checkpoints()

    assert pending == []


@pytest.mark.asyncio
async def test_get_pending_checkpoints_can_disable_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_OPTION_QUOTE_TRACKER_PREFER_HEBER", "false")

    async def _fail_db_query(_fn):
        raise AssertionError("local fallback should not be called")

    monkeypatch.setattr(oqt, "db_query", _fail_db_query, raising=False)

    pending = await oqt.get_pending_checkpoints()

    assert pending == []


@pytest.mark.asyncio
async def test_store_quote_and_get_existing_quotes_use_in_memory_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail_db_query(_fn):
        raise AssertionError("local db_query should not be used")

    async def _fail_db_write(_fn):
        raise AssertionError("local db_write should not be used")

    monkeypatch.setattr(oqt, "db_query", _fail_db_query, raising=False)
    monkeypatch.setattr(oqt, "db_write", _fail_db_write, raising=False)
    monkeypatch.setattr(oqt, "_quote_checkpoint_cache", {}, raising=False)

    await oqt.store_quote(
        flow_event_id="evt-1",
        option_symbol="SPY260220C00500000",
        underlying_ticker="SPY",
        checkpoint="15m",
        ts_utc=datetime.now(UTC),
        quote_data={"mid_price": 1.23},
    )

    existing = await oqt.get_existing_quotes(["evt-1", "evt-2"])

    assert existing == {"evt-1": {"15m"}}
