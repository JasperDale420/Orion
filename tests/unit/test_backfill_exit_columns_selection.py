from __future__ import annotations

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
