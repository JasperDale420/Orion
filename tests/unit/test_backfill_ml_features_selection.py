from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from orion.jobs import backfill_ml_features


@pytest.mark.asyncio
async def test_get_records_to_backfill_uses_deterministic_ordering(
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

    monkeypatch.setattr(backfill_ml_features, "db_query", _fake_db_query)

    records = await backfill_ml_features.get_records_to_backfill(limit=25)

    assert records == []
    assert "ORDER BY p.entry_ts ASC, p.event_id ASC" in captured["sql"]
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

    monkeypatch.setattr(backfill_ml_features, "db_query", _fake_db_query)

    cursor_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    records = await backfill_ml_features.get_records_to_backfill(
        limit=10,
        after_entry_ts=cursor_ts,
        after_event_id="evt-100",
    )

    assert records == []
    assert "p.entry_ts > :after_entry_ts" in captured["sql"]
    assert "p.entry_ts = :after_entry_ts AND p.event_id > :after_event_id" in captured["sql"]
    assert captured["params"]["after_entry_ts"] == cursor_ts
    assert captured["params"]["after_event_id"] == "evt-100"


@pytest.mark.asyncio
async def test_get_records_to_backfill_supports_timestamp_only_cursor_filter(
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

    monkeypatch.setattr(backfill_ml_features, "db_query", _fake_db_query)

    cursor_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    records = await backfill_ml_features.get_records_to_backfill(
        limit=10,
        after_entry_ts=cursor_ts,
        after_event_id=None,
    )

    assert records == []
    assert "p.entry_ts >= :after_entry_ts" in captured["sql"]
    assert captured["params"]["after_entry_ts"] == cursor_ts
    assert "after_event_id" not in captured["params"]


@pytest.mark.asyncio
async def test_run_backfill_uses_cursor_to_paginate_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    first_ts = datetime(2026, 2, 9, 14, 0, tzinfo=timezone.utc)
    second_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    async def _fake_get_records_to_backfill(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        calls.append(
            {
                "limit": limit,
                "after_entry_ts": after_entry_ts,
                "after_event_id": after_event_id,
            }
        )
        if after_entry_ts is None and after_event_id is None:
            return [
                {"event_id": "evt-1", "ticker": "AAPL", "entry_ts": first_ts},
                {"event_id": "evt-2", "ticker": "MSFT", "entry_ts": second_ts},
            ]
        if after_entry_ts == second_ts and after_event_id == "evt-2":
            return [{"event_id": "evt-3", "ticker": "TSLA", "entry_ts": second_ts}]
        return []

    async def _fake_update_ml_features(_record: dict[str, Any]) -> bool:
        return True

    async def _fake_init_db() -> None:
        return None

    async def _fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(backfill_ml_features, "get_records_to_backfill", _fake_get_records_to_backfill)
    monkeypatch.setattr(backfill_ml_features, "update_ml_features", _fake_update_ml_features)
    monkeypatch.setattr(backfill_ml_features, "init_db", _fake_init_db)
    monkeypatch.setattr(backfill_ml_features.asyncio, "sleep", _fake_sleep)

    await backfill_ml_features.run_backfill(batch_size=2, limit=3)

    assert calls[0]["after_entry_ts"] is None
    assert calls[0]["after_event_id"] is None
    assert calls[1]["after_entry_ts"] == second_ts
    assert calls[1]["after_event_id"] == "evt-2"


@pytest.mark.asyncio
async def test_run_backfill_requests_only_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    first_ts = datetime(2026, 2, 9, 14, 0, tzinfo=timezone.utc)
    second_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    async def _fake_get_records_to_backfill(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        calls.append(
            {
                "limit": limit,
                "after_entry_ts": after_entry_ts,
                "after_event_id": after_event_id,
            }
        )
        if len(calls) == 1:
            return [
                {"event_id": "evt-1", "ticker": "AAPL", "entry_ts": first_ts},
                {"event_id": "evt-2", "ticker": "MSFT", "entry_ts": second_ts},
            ]
        if len(calls) == 2:
            return [{"event_id": "evt-3", "ticker": "TSLA", "entry_ts": second_ts}]
        return []

    async def _fake_update_ml_features(_record: dict[str, Any]) -> bool:
        return True

    async def _fake_init_db() -> None:
        return None

    async def _fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(backfill_ml_features, "get_records_to_backfill", _fake_get_records_to_backfill)
    monkeypatch.setattr(backfill_ml_features, "update_ml_features", _fake_update_ml_features)
    monkeypatch.setattr(backfill_ml_features, "init_db", _fake_init_db)
    monkeypatch.setattr(backfill_ml_features.asyncio, "sleep", _fake_sleep)

    await backfill_ml_features.run_backfill(batch_size=2, limit=3)

    assert calls[0]["limit"] == 2
    assert calls[1]["limit"] == 1
    assert calls[1]["after_entry_ts"] == second_ts
    assert calls[1]["after_event_id"] == "evt-2"


@pytest.mark.asyncio
async def test_run_backfill_resumes_from_watermark_and_persists_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    saved_watermarks: list[datetime] = []
    resumed_ts = datetime(2026, 2, 9, 13, 0, tzinfo=timezone.utc)
    first_ts = datetime(2026, 2, 9, 14, 0, tzinfo=timezone.utc)
    second_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    async def _fake_get_records_to_backfill(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        calls.append(
            {
                "limit": limit,
                "after_entry_ts": after_entry_ts,
                "after_event_id": after_event_id,
            }
        )
        if len(calls) == 1:
            return [
                {"event_id": "evt-1", "ticker": "AAPL", "entry_ts": first_ts},
                {"event_id": "evt-2", "ticker": "MSFT", "entry_ts": second_ts},
            ]
        return []

    async def _fake_update_ml_features(_record: dict[str, Any]) -> bool:
        return True

    async def _fake_init_db() -> None:
        return None

    async def _fake_sleep(_seconds: float) -> None:
        return None

    async def _fake_load_backfill_cursor() -> tuple[datetime | None, str | None]:
        return resumed_ts, None

    async def _fake_save_backfill_cursor(entry_ts: datetime, _event_id: str | None) -> None:
        saved_watermarks.append(entry_ts)

    monkeypatch.setattr(backfill_ml_features, "get_records_to_backfill", _fake_get_records_to_backfill)
    monkeypatch.setattr(backfill_ml_features, "update_ml_features", _fake_update_ml_features)
    monkeypatch.setattr(backfill_ml_features, "init_db", _fake_init_db)
    monkeypatch.setattr(backfill_ml_features.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(backfill_ml_features, "_load_backfill_cursor", _fake_load_backfill_cursor, raising=False)
    monkeypatch.setattr(backfill_ml_features, "_save_backfill_cursor", _fake_save_backfill_cursor, raising=False)

    await backfill_ml_features.run_backfill(batch_size=2, limit=2)

    assert calls[0]["after_entry_ts"] == resumed_ts
    assert calls[0]["after_event_id"] is None
    assert saved_watermarks == [first_ts, second_ts]


@pytest.mark.asyncio
async def test_run_backfill_resumes_with_keyset_cursor_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    saved_cursors: list[tuple[datetime, str]] = []

    resumed_ts = datetime(2026, 2, 9, 13, 0, tzinfo=timezone.utc)
    first_ts = datetime(2026, 2, 9, 14, 0, tzinfo=timezone.utc)

    async def _fake_get_records_to_backfill(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        calls.append(
            {
                "limit": limit,
                "after_entry_ts": after_entry_ts,
                "after_event_id": after_event_id,
            }
        )
        if len(calls) == 1:
            return [{"event_id": "evt-200", "ticker": "AAPL", "entry_ts": first_ts}]
        return []

    async def _fake_update_ml_features(_record: dict[str, Any]) -> bool:
        return True

    async def _fake_init_db() -> None:
        return None

    async def _fake_sleep(_seconds: float) -> None:
        return None

    async def _fake_load_backfill_cursor() -> tuple[datetime | None, str | None]:
        return resumed_ts, "evt-150"

    async def _fake_save_backfill_cursor(entry_ts: datetime, event_id: str | None) -> None:
        assert event_id is not None
        saved_cursors.append((entry_ts, event_id))

    monkeypatch.setattr(backfill_ml_features, "get_records_to_backfill", _fake_get_records_to_backfill)
    monkeypatch.setattr(backfill_ml_features, "update_ml_features", _fake_update_ml_features)
    monkeypatch.setattr(backfill_ml_features, "init_db", _fake_init_db)
    monkeypatch.setattr(backfill_ml_features.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(backfill_ml_features, "_load_backfill_cursor", _fake_load_backfill_cursor, raising=False)
    monkeypatch.setattr(backfill_ml_features, "_save_backfill_cursor", _fake_save_backfill_cursor, raising=False)

    await backfill_ml_features.run_backfill(batch_size=2, limit=1)

    assert calls[0]["after_entry_ts"] == resumed_ts
    assert calls[0]["after_event_id"] == "evt-150"
    assert saved_cursors == [(first_ts, "evt-200")]
