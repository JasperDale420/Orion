from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from orion.jobs import backfill_exit_columns


@pytest.mark.asyncio
async def test_get_records_to_backfill_delegates_to_labeler(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    expected = [
        {"event_id": "vel-1", "entry_ts": datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)},
    ]

    async def _fake_delegate(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        captured["limit"] = limit
        captured["after_entry_ts"] = after_entry_ts
        captured["after_event_id"] = after_event_id
        return expected

    async def _fail_db_query(_query):
        raise AssertionError("local db_query should not be used for velocity candidate selection")

    monkeypatch.setattr(
        backfill_exit_columns,
        "get_labeler_velocity_backfill_candidates",
        _fake_delegate,
        raising=False,
    )
    monkeypatch.setattr(backfill_exit_columns, "db_query", _fail_db_query, raising=False)

    records = await backfill_exit_columns.get_records_to_backfill(limit=15)

    assert records == expected
    assert captured == {
        "limit": 15,
        "after_entry_ts": None,
        "after_event_id": None,
    }


@pytest.mark.asyncio
async def test_get_all_records_for_checkpoints_delegates_to_labeler(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    expected = [
        {"event_id": "cp-1", "entry_ts": datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)},
    ]

    async def _fake_delegate(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        captured["limit"] = limit
        captured["after_entry_ts"] = after_entry_ts
        captured["after_event_id"] = after_event_id
        return expected

    async def _fail_db_query(_query):
        raise AssertionError("local db_query should not be used for checkpoint candidate selection")

    monkeypatch.setattr(
        backfill_exit_columns,
        "get_labeler_checkpoint_backfill_candidates",
        _fake_delegate,
        raising=False,
    )
    monkeypatch.setattr(backfill_exit_columns, "db_query", _fail_db_query, raising=False)

    records = await backfill_exit_columns.get_all_records_for_checkpoints(limit=25)

    assert records == expected
    assert captured == {
        "limit": 25,
        "after_entry_ts": None,
        "after_event_id": None,
    }


@pytest.mark.asyncio
async def test_get_subsequent_prices_delegates_to_labeler(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    captured: dict[str, Any] = {}
    expected = [
        {"price": 1.25, "ts": datetime(2026, 2, 9, 15, 5, tzinfo=timezone.utc)},
        {"price": 1.31, "ts": datetime(2026, 2, 9, 15, 10, tzinfo=timezone.utc)},
    ]

    async def _labeler_subsequent_prices(option_chain: str, ts: datetime) -> list[dict[str, Any]]:
        captured["option_chain"] = option_chain
        captured["entry_ts"] = ts
        return expected

    async def _fail_db_query(_query):
        raise AssertionError("local db_query should not be used for subsequent price lookup")

    monkeypatch.setattr(
        backfill_exit_columns,
        "get_labeler_subsequent_prices",
        _labeler_subsequent_prices,
        raising=False,
    )
    monkeypatch.setattr(backfill_exit_columns, "db_query", _fail_db_query, raising=False)

    value = await backfill_exit_columns.get_subsequent_prices("AAPL260221C00100000", entry_ts)

    assert value == expected
    assert captured == {
        "option_chain": "AAPL260221C00100000",
        "entry_ts": entry_ts,
    }


@pytest.mark.asyncio
async def test_get_records_to_backfill_supports_cursor_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_delegate(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        captured["limit"] = limit
        captured["after_entry_ts"] = after_entry_ts
        captured["after_event_id"] = after_event_id
        return []

    monkeypatch.setattr(
        backfill_exit_columns,
        "get_labeler_velocity_backfill_candidates",
        _fake_delegate,
        raising=False,
    )

    cursor_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    records = await backfill_exit_columns.get_records_to_backfill(
        limit=10,
        after_entry_ts=cursor_ts,
        after_event_id="evt-100",
    )

    assert records == []
    assert captured["limit"] == 10
    assert captured["after_entry_ts"] == cursor_ts
    assert captured["after_event_id"] == "evt-100"


@pytest.mark.asyncio
async def test_get_all_records_for_checkpoints_supports_cursor_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_delegate(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        captured["limit"] = limit
        captured["after_entry_ts"] = after_entry_ts
        captured["after_event_id"] = after_event_id
        return []

    monkeypatch.setattr(
        backfill_exit_columns,
        "get_labeler_checkpoint_backfill_candidates",
        _fake_delegate,
        raising=False,
    )

    cursor_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    records = await backfill_exit_columns.get_all_records_for_checkpoints(
        limit=10,
        after_entry_ts=cursor_ts,
        after_event_id="evt-100",
    )

    assert records == []
    assert captured["limit"] == 10
    assert captured["after_entry_ts"] == cursor_ts
    assert captured["after_event_id"] == "evt-100"


@pytest.mark.asyncio
async def test_get_records_to_backfill_supports_timestamp_only_cursor_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_delegate(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        captured["limit"] = limit
        captured["after_entry_ts"] = after_entry_ts
        captured["after_event_id"] = after_event_id
        return []

    monkeypatch.setattr(
        backfill_exit_columns,
        "get_labeler_velocity_backfill_candidates",
        _fake_delegate,
        raising=False,
    )

    cursor_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    records = await backfill_exit_columns.get_records_to_backfill(
        limit=10,
        after_entry_ts=cursor_ts,
        after_event_id=None,
    )

    assert records == []
    assert captured["limit"] == 10
    assert captured["after_entry_ts"] == cursor_ts
    assert captured["after_event_id"] is None


@pytest.mark.asyncio
async def test_get_all_records_for_checkpoints_supports_timestamp_only_cursor_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_delegate(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        captured["limit"] = limit
        captured["after_entry_ts"] = after_entry_ts
        captured["after_event_id"] = after_event_id
        return []

    monkeypatch.setattr(
        backfill_exit_columns,
        "get_labeler_checkpoint_backfill_candidates",
        _fake_delegate,
        raising=False,
    )

    cursor_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    records = await backfill_exit_columns.get_all_records_for_checkpoints(
        limit=10,
        after_entry_ts=cursor_ts,
        after_event_id=None,
    )

    assert records == []
    assert captured["limit"] == 10
    assert captured["after_entry_ts"] == cursor_ts
    assert captured["after_event_id"] is None


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


@pytest.mark.asyncio
async def test_run_backfill_resumes_from_phase_watermarks_and_persists_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    velocity_calls: list[dict[str, Any]] = []
    checkpoint_calls: list[dict[str, Any]] = []
    saved_velocity_watermarks: list[datetime] = []
    saved_checkpoint_watermarks: list[datetime] = []

    resumed_velocity_ts = datetime(2026, 2, 9, 13, 0, tzinfo=timezone.utc)
    resumed_checkpoint_ts = datetime(2026, 2, 9, 13, 30, tzinfo=timezone.utc)
    vel_ts1 = datetime(2026, 2, 9, 14, 0, tzinfo=timezone.utc)
    vel_ts2 = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    cp_ts1 = datetime(2026, 2, 9, 16, 0, tzinfo=timezone.utc)
    cp_ts2 = datetime(2026, 2, 9, 17, 0, tzinfo=timezone.utc)

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
                {"event_id": "vel-1", "entry_ts": vel_ts1},
                {"event_id": "vel-2", "entry_ts": vel_ts2},
            ]
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
                {"event_id": "cp-1", "entry_ts": cp_ts1, "option_chain": "AAPL", "entry_option_price": 1.0},
                {"event_id": "cp-2", "entry_ts": cp_ts2, "option_chain": "MSFT", "entry_option_price": 1.0},
            ]
        return []

    async def _fake_update_velocity_columns(_record: dict[str, Any]) -> bool:
        return True

    async def _fake_update_checkpoint_columns(_record: dict[str, Any]) -> bool:
        return True

    async def _fake_init_db() -> None:
        return None

    async def _fake_load_velocity_backfill_cursor() -> tuple[datetime | None, str | None]:
        return resumed_velocity_ts, None

    async def _fake_save_velocity_backfill_cursor(entry_ts: datetime, _event_id: str | None) -> None:
        saved_velocity_watermarks.append(entry_ts)

    async def _fake_load_checkpoint_backfill_cursor() -> tuple[datetime | None, str | None]:
        return resumed_checkpoint_ts, None

    async def _fake_save_checkpoint_backfill_cursor(entry_ts: datetime, _event_id: str | None) -> None:
        saved_checkpoint_watermarks.append(entry_ts)

    monkeypatch.setattr(backfill_exit_columns, "get_records_to_backfill", _fake_get_records_to_backfill)
    monkeypatch.setattr(backfill_exit_columns, "get_all_records_for_checkpoints", _fake_get_all_records_for_checkpoints)
    monkeypatch.setattr(backfill_exit_columns, "update_velocity_columns", _fake_update_velocity_columns)
    monkeypatch.setattr(backfill_exit_columns, "update_checkpoint_columns", _fake_update_checkpoint_columns)
    monkeypatch.setattr(backfill_exit_columns, "init_db", _fake_init_db)
    monkeypatch.setattr(
        backfill_exit_columns,
        "_load_velocity_backfill_cursor",
        _fake_load_velocity_backfill_cursor,
        raising=False,
    )
    monkeypatch.setattr(
        backfill_exit_columns,
        "_save_velocity_backfill_cursor",
        _fake_save_velocity_backfill_cursor,
        raising=False,
    )
    monkeypatch.setattr(
        backfill_exit_columns,
        "_load_checkpoint_backfill_cursor",
        _fake_load_checkpoint_backfill_cursor,
        raising=False,
    )
    monkeypatch.setattr(
        backfill_exit_columns,
        "_save_checkpoint_backfill_cursor",
        _fake_save_checkpoint_backfill_cursor,
        raising=False,
    )

    await backfill_exit_columns.run_backfill(batch_size=2, limit=2)

    assert velocity_calls[0]["after_entry_ts"] == resumed_velocity_ts
    assert velocity_calls[0]["after_event_id"] is None
    assert checkpoint_calls[0]["after_entry_ts"] == resumed_checkpoint_ts
    assert checkpoint_calls[0]["after_event_id"] is None
    assert saved_velocity_watermarks == [vel_ts1, vel_ts2]
    assert saved_checkpoint_watermarks == [cp_ts1, cp_ts2]


@pytest.mark.asyncio
async def test_run_backfill_resumes_with_keyset_cursor_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    velocity_calls: list[dict[str, Any]] = []
    checkpoint_calls: list[dict[str, Any]] = []
    saved_velocity_cursors: list[tuple[datetime, str]] = []
    saved_checkpoint_cursors: list[tuple[datetime, str]] = []

    resumed_velocity_ts = datetime(2026, 2, 9, 13, 0, tzinfo=timezone.utc)
    resumed_checkpoint_ts = datetime(2026, 2, 9, 13, 30, tzinfo=timezone.utc)
    vel_ts = datetime(2026, 2, 9, 14, 0, tzinfo=timezone.utc)
    cp_ts = datetime(2026, 2, 9, 16, 0, tzinfo=timezone.utc)

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
            return [{"event_id": "vel-200", "entry_ts": vel_ts}]
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
            return [{"event_id": "cp-200", "entry_ts": cp_ts, "option_chain": "AAPL", "entry_option_price": 1.0}]
        return []

    async def _fake_update_velocity_columns(_record: dict[str, Any]) -> bool:
        return True

    async def _fake_update_checkpoint_columns(_record: dict[str, Any]) -> bool:
        return True

    async def _fake_init_db() -> None:
        return None

    async def _fake_load_velocity_backfill_cursor() -> tuple[datetime | None, str | None]:
        return resumed_velocity_ts, "vel-150"

    async def _fake_load_checkpoint_backfill_cursor() -> tuple[datetime | None, str | None]:
        return resumed_checkpoint_ts, "cp-150"

    async def _fake_save_velocity_backfill_cursor(entry_ts: datetime, event_id: str | None) -> None:
        assert event_id is not None
        saved_velocity_cursors.append((entry_ts, event_id))

    async def _fake_save_checkpoint_backfill_cursor(entry_ts: datetime, event_id: str | None) -> None:
        assert event_id is not None
        saved_checkpoint_cursors.append((entry_ts, event_id))

    monkeypatch.setattr(backfill_exit_columns, "get_records_to_backfill", _fake_get_records_to_backfill)
    monkeypatch.setattr(backfill_exit_columns, "get_all_records_for_checkpoints", _fake_get_all_records_for_checkpoints)
    monkeypatch.setattr(backfill_exit_columns, "update_velocity_columns", _fake_update_velocity_columns)
    monkeypatch.setattr(backfill_exit_columns, "update_checkpoint_columns", _fake_update_checkpoint_columns)
    monkeypatch.setattr(backfill_exit_columns, "init_db", _fake_init_db)
    monkeypatch.setattr(
        backfill_exit_columns,
        "_load_velocity_backfill_cursor",
        _fake_load_velocity_backfill_cursor,
        raising=False,
    )
    monkeypatch.setattr(
        backfill_exit_columns,
        "_load_checkpoint_backfill_cursor",
        _fake_load_checkpoint_backfill_cursor,
        raising=False,
    )
    monkeypatch.setattr(
        backfill_exit_columns,
        "_save_velocity_backfill_cursor",
        _fake_save_velocity_backfill_cursor,
        raising=False,
    )
    monkeypatch.setattr(
        backfill_exit_columns,
        "_save_checkpoint_backfill_cursor",
        _fake_save_checkpoint_backfill_cursor,
        raising=False,
    )

    await backfill_exit_columns.run_backfill(batch_size=2, limit=1)

    assert velocity_calls[0]["after_entry_ts"] == resumed_velocity_ts
    assert velocity_calls[0]["after_event_id"] == "vel-150"
    assert checkpoint_calls[0]["after_entry_ts"] == resumed_checkpoint_ts
    assert checkpoint_calls[0]["after_event_id"] == "cp-150"
    assert saved_velocity_cursors == [(vel_ts, "vel-200")]
    assert saved_checkpoint_cursors == [(cp_ts, "cp-200")]


@pytest.mark.asyncio
async def test_load_phase_cursors_do_not_fallback_to_legacy_watermarks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_get_cursor_state(_session: Any, _key: str) -> None:
        return None

    async def _fake_get_watermark(_session: Any, _key: str) -> None:
        raise AssertionError("legacy watermark fallback should not be read")

    class _FakeSession:
        pass

    async def _fake_db_query(fn):
        return await fn(_FakeSession())

    monkeypatch.setattr(backfill_exit_columns, "get_cursor_state", _fake_get_cursor_state)
    monkeypatch.setattr(backfill_exit_columns, "get_watermark", _fake_get_watermark, raising=False)
    monkeypatch.setattr(backfill_exit_columns, "db_query", _fake_db_query)

    velocity_loaded = await backfill_exit_columns._load_velocity_backfill_cursor()
    checkpoint_loaded = await backfill_exit_columns._load_checkpoint_backfill_cursor()

    assert velocity_loaded == (None, None)
    assert checkpoint_loaded == (None, None)


@pytest.mark.asyncio
async def test_save_phase_cursors_do_not_write_legacy_watermarks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    velocity_writes: list[tuple[datetime, str | None]] = []
    checkpoint_writes: list[tuple[datetime, str | None]] = []

    async def _fake_upsert_cursor_state(
        _session: Any,
        key: str,
        last_seen_ts_utc: datetime,
        last_seen_id: str | None,
    ) -> None:
        if key == backfill_exit_columns.VELOCITY_BACKFILL_CURSOR_KEY:
            velocity_writes.append((last_seen_ts_utc, last_seen_id))
            return
        if key == backfill_exit_columns.CHECKPOINT_BACKFILL_CURSOR_KEY:
            checkpoint_writes.append((last_seen_ts_utc, last_seen_id))
            return
        raise AssertionError(f"Unexpected cursor key: {key}")

    async def _fake_upsert_watermark(_session: Any, _key: str, _last_seen_ts_utc: datetime) -> None:
        raise AssertionError("legacy watermark fallback should not be written")

    class _FakeSession:
        pass

    async def _fake_db_write(fn):
        return await fn(_FakeSession())

    ts1 = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 2, 9, 16, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(backfill_exit_columns, "upsert_cursor_state", _fake_upsert_cursor_state)
    monkeypatch.setattr(backfill_exit_columns, "upsert_watermark", _fake_upsert_watermark, raising=False)
    monkeypatch.setattr(backfill_exit_columns, "db_write", _fake_db_write)

    await backfill_exit_columns._save_velocity_backfill_cursor(ts1, "vel-500")
    await backfill_exit_columns._save_checkpoint_backfill_cursor(ts2, "cp-500")

    assert velocity_writes == [(ts1, "vel-500")]
    assert checkpoint_writes == [(ts2, "cp-500")]
