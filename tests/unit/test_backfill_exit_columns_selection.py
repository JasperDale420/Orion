from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from orion.jobs import backfill_exit_columns


@pytest.mark.asyncio
async def test_get_records_to_backfill_targets_all_velocity_columns_with_ordering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeResult:
        def fetchall(self) -> list[Any]:
            return []

    class _FakeSession:
        async def execute(self, stmt: Any, params: dict[str, Any]) -> _FakeResult:
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _FakeResult()

    async def _fake_db_query(fn):
        return await fn(_FakeSession())

    monkeypatch.setattr(backfill_exit_columns, "db_query", _fake_db_query)

    records = await backfill_exit_columns.get_records_to_backfill(limit=15)

    assert records == []
    assert "time_to_75_pct_seconds IS NULL" in captured["sql"]
    assert "time_to_100_pct_seconds IS NULL" in captured["sql"]
    assert "time_to_150_pct_seconds IS NULL" in captured["sql"]
    assert "ORDER BY entry_ts ASC, event_id ASC" in captured["sql"]
    assert captured["params"]["limit"] == 15


@pytest.mark.asyncio
async def test_get_all_records_for_checkpoints_targets_any_missing_checkpoint_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeResult:
        def fetchall(self) -> list[Any]:
            return []

    class _FakeSession:
        async def execute(self, stmt: Any, params: dict[str, Any]) -> _FakeResult:
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _FakeResult()

    async def _fake_db_query(fn):
        return await fn(_FakeSession())

    monkeypatch.setattr(backfill_exit_columns, "db_query", _fake_db_query)

    records = await backfill_exit_columns.get_all_records_for_checkpoints(limit=25)

    assert records == []
    assert "price_at_15m IS NULL" in captured["sql"]
    assert "return_at_15m IS NULL" in captured["sql"]
    assert "price_at_30m IS NULL" in captured["sql"]
    assert "return_at_30m IS NULL" in captured["sql"]
    assert "price_at_8h IS NULL" in captured["sql"]
    assert "return_at_8h IS NULL" in captured["sql"]
    assert "price_at_1d IS NULL" in captured["sql"]
    assert "return_at_1d IS NULL" in captured["sql"]
    assert "price_at_2d IS NULL" in captured["sql"]
    assert "return_at_2d IS NULL" in captured["sql"]
    assert "price_at_3d IS NULL" in captured["sql"]
    assert "return_at_3d IS NULL" in captured["sql"]
    assert "price_at_1w IS NULL" in captured["sql"]
    assert "return_at_1w IS NULL" in captured["sql"]
    assert "ORDER BY entry_ts ASC, event_id ASC" in captured["sql"]
    assert captured["params"]["limit"] == 25


@pytest.mark.asyncio
async def test_get_records_to_backfill_supports_cursor_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeResult:
        def fetchall(self) -> list[Any]:
            return []

    class _FakeSession:
        async def execute(self, stmt: Any, params: dict[str, Any]) -> _FakeResult:
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _FakeResult()

    async def _fake_db_query(fn):
        return await fn(_FakeSession())

    monkeypatch.setattr(backfill_exit_columns, "db_query", _fake_db_query)

    cursor_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    records = await backfill_exit_columns.get_records_to_backfill(
        limit=10,
        after_entry_ts=cursor_ts,
        after_event_id="evt-100",
    )

    assert records == []
    assert "entry_ts > :after_entry_ts" in captured["sql"]
    assert "entry_ts = :after_entry_ts AND event_id > :after_event_id" in captured["sql"]
    assert captured["params"]["after_entry_ts"] == cursor_ts
    assert captured["params"]["after_event_id"] == "evt-100"


@pytest.mark.asyncio
async def test_get_all_records_for_checkpoints_supports_cursor_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeResult:
        def fetchall(self) -> list[Any]:
            return []

    class _FakeSession:
        async def execute(self, stmt: Any, params: dict[str, Any]) -> _FakeResult:
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _FakeResult()

    async def _fake_db_query(fn):
        return await fn(_FakeSession())

    monkeypatch.setattr(backfill_exit_columns, "db_query", _fake_db_query)

    cursor_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    records = await backfill_exit_columns.get_all_records_for_checkpoints(
        limit=10,
        after_entry_ts=cursor_ts,
        after_event_id="evt-100",
    )

    assert records == []
    assert "entry_ts > :after_entry_ts" in captured["sql"]
    assert "entry_ts = :after_entry_ts AND event_id > :after_event_id" in captured["sql"]
    assert captured["params"]["after_entry_ts"] == cursor_ts
    assert captured["params"]["after_event_id"] == "evt-100"


@pytest.mark.asyncio
async def test_run_backfill_paginates_velocity_and_checkpoint_phases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    velocity_calls: list[dict[str, Any]] = []
    checkpoint_calls: list[dict[str, Any]] = []

    ts1 = datetime(2026, 2, 9, 14, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    async def _fake_get_records_to_backfill(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        velocity_calls.append(
            {
                "limit": limit,
                "after_entry_ts": after_entry_ts,
                "after_event_id": after_event_id,
            }
        )
        if len(velocity_calls) == 1:
            return [
                {"event_id": "vel-1", "entry_ts": ts1},
                {"event_id": "vel-2", "entry_ts": ts2},
            ]
        if len(velocity_calls) == 2:
            return [{"event_id": "vel-3", "entry_ts": ts2}]
        return []

    async def _fake_get_all_records_for_checkpoints(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        checkpoint_calls.append(
            {
                "limit": limit,
                "after_entry_ts": after_entry_ts,
                "after_event_id": after_event_id,
            }
        )
        if len(checkpoint_calls) == 1:
            return [
                {"event_id": "cp-1", "entry_ts": ts1, "option_chain": "AAPL", "entry_option_price": 1.0},
                {"event_id": "cp-2", "entry_ts": ts2, "option_chain": "MSFT", "entry_option_price": 1.0},
            ]
        if len(checkpoint_calls) == 2:
            return [{"event_id": "cp-3", "entry_ts": ts2, "option_chain": "TSLA", "entry_option_price": 1.0}]
        return []

    async def _fake_update_velocity_columns(_record: dict[str, Any]) -> bool:
        return True

    async def _fake_update_checkpoint_columns(_record: dict[str, Any]) -> bool:
        return True

    async def _fake_init_db() -> None:
        return None

    monkeypatch.setattr(backfill_exit_columns, "get_records_to_backfill", _fake_get_records_to_backfill)
    monkeypatch.setattr(backfill_exit_columns, "get_all_records_for_checkpoints", _fake_get_all_records_for_checkpoints)
    monkeypatch.setattr(backfill_exit_columns, "update_velocity_columns", _fake_update_velocity_columns)
    monkeypatch.setattr(backfill_exit_columns, "update_checkpoint_columns", _fake_update_checkpoint_columns)
    monkeypatch.setattr(backfill_exit_columns, "init_db", _fake_init_db)

    await backfill_exit_columns.run_backfill(batch_size=2, limit=3)

    assert velocity_calls[0]["limit"] == 2
    assert velocity_calls[1]["limit"] == 1
    assert velocity_calls[1]["after_entry_ts"] == ts2
    assert velocity_calls[1]["after_event_id"] == "vel-2"

    assert checkpoint_calls[0]["limit"] == 2
    assert checkpoint_calls[1]["limit"] == 1
    assert checkpoint_calls[1]["after_entry_ts"] == ts2
    assert checkpoint_calls[1]["after_event_id"] == "cp-2"
