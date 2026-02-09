from __future__ import annotations

import gzip
import itertools
import json
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


@pytest.mark.asyncio
async def test_update_record_with_retry_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = {"count": 0}
    sleeps: list[float] = []

    async def _flaky_update(_record: dict[str, Any]) -> bool:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("transient failure")
        return True

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(backfill_exit_columns.asyncio, "sleep", _fake_sleep)

    updated, failed, retries, error_message = await backfill_exit_columns._update_record_with_retry(
        {"event_id": "evt-1"},
        _flaky_update,
        phase_name="velocity",
    )

    assert updated is True
    assert failed is False
    assert retries == 1
    assert error_message is None
    assert attempts["count"] == 2
    assert sleeps == [backfill_exit_columns.RETRY_SLEEP_SECONDS]


@pytest.mark.asyncio
async def test_update_record_with_retry_marks_failure_after_max_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = {"count": 0}
    sleeps: list[float] = []

    async def _always_fail(_record: dict[str, Any]) -> bool:
        attempts["count"] += 1
        raise RuntimeError("permanent failure")

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(backfill_exit_columns.asyncio, "sleep", _fake_sleep)

    updated, failed, retries, error_message = await backfill_exit_columns._update_record_with_retry(
        {"event_id": "evt-2"},
        _always_fail,
        phase_name="checkpoint",
    )

    assert updated is False
    assert failed is True
    assert retries == backfill_exit_columns.MAX_RECORD_RETRIES
    assert error_message == "permanent failure"
    assert attempts["count"] == backfill_exit_columns.MAX_RECORD_RETRIES + 1
    assert len(sleeps) == backfill_exit_columns.MAX_RECORD_RETRIES


@pytest.mark.asyncio
async def test_run_backfill_continues_when_velocity_update_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    velocity_calls: list[dict[str, Any]] = []
    saved_velocity_cursors: list[tuple[datetime, str | None]] = []
    attempted_event_ids: list[str] = []

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
                {"event_id": "vel-err", "entry_ts": ts1},
                {"event_id": "vel-ok", "entry_ts": ts2},
            ]
        return []

    async def _fake_get_all_records_for_checkpoints(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return []

    async def _fake_update_velocity_columns(record: dict[str, Any]) -> bool:
        attempted_event_ids.append(record["event_id"])
        if record["event_id"] == "vel-err":
            raise RuntimeError("boom")
        return True

    async def _fake_update_checkpoint_columns(_record: dict[str, Any]) -> bool:
        return True

    async def _fake_init_db() -> None:
        return None

    async def _fake_load_velocity_backfill_cursor() -> tuple[datetime | None, str | None]:
        return None, None

    async def _fake_load_checkpoint_backfill_cursor() -> tuple[datetime | None, str | None]:
        return None, None

    async def _fake_save_velocity_backfill_cursor(entry_ts: datetime, event_id: str | None) -> None:
        saved_velocity_cursors.append((entry_ts, event_id))

    async def _fake_save_checkpoint_backfill_cursor(_entry_ts: datetime, _event_id: str | None) -> None:
        raise AssertionError("checkpoint cursor should not be written")

    async def _fake_sleep(_seconds: float) -> None:
        return None

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
    monkeypatch.setattr(backfill_exit_columns.asyncio, "sleep", _fake_sleep)

    summary = await backfill_exit_columns.run_backfill(batch_size=2, limit=2)

    assert attempted_event_ids.count("vel-err") == backfill_exit_columns.MAX_RECORD_RETRIES + 1
    assert "vel-ok" in attempted_event_ids
    assert saved_velocity_cursors == [(ts1, "vel-err"), (ts2, "vel-ok")]
    assert summary["velocity"]["processed"] == 2
    assert summary["velocity"]["updated"] == 1
    assert summary["velocity"]["failed"] == 1
    assert summary["total_failed"] == 1


@pytest.mark.asyncio
async def test_run_backfill_writes_dead_letter_for_exhausted_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    dead_letter_path = tmp_path / "exit_backfill_dead_letter.jsonl"
    ts1 = datetime(2026, 2, 9, 14, 0, tzinfo=timezone.utc)

    async def _fake_get_records_to_backfill(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if after_entry_ts is None:
            return [{"event_id": "vel-fail", "entry_ts": ts1}]
        return []

    async def _fake_get_all_records_for_checkpoints(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return []

    async def _always_fail_velocity(_record: dict[str, Any]) -> bool:
        raise RuntimeError("dead-letter-me")

    async def _fake_update_checkpoint_columns(_record: dict[str, Any]) -> bool:
        return True

    async def _fake_init_db() -> None:
        return None

    async def _fake_load_velocity_backfill_cursor() -> tuple[datetime | None, str | None]:
        return None, None

    async def _fake_load_checkpoint_backfill_cursor() -> tuple[datetime | None, str | None]:
        return None, None

    async def _fake_save_velocity_backfill_cursor(_entry_ts: datetime, _event_id: str | None) -> None:
        return None

    async def _fake_save_checkpoint_backfill_cursor(_entry_ts: datetime, _event_id: str | None) -> None:
        return None

    async def _fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(backfill_exit_columns, "get_records_to_backfill", _fake_get_records_to_backfill)
    monkeypatch.setattr(backfill_exit_columns, "get_all_records_for_checkpoints", _fake_get_all_records_for_checkpoints)
    monkeypatch.setattr(backfill_exit_columns, "update_velocity_columns", _always_fail_velocity)
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
    monkeypatch.setattr(backfill_exit_columns.asyncio, "sleep", _fake_sleep)

    summary = await backfill_exit_columns.run_backfill(
        batch_size=1,
        limit=1,
        max_retries=1,
        dead_letter_path=str(dead_letter_path),
    )

    assert summary["velocity"]["failed"] == 1
    assert summary["velocity"]["dead_lettered"] == 1
    assert summary["total_dead_lettered"] == 1
    lines = dead_letter_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["phase"] == "velocity"
    assert payload["event_id"] == "vel-fail"
    assert payload["retries_used"] == 1
    assert payload["error"] == "dead-letter-me"


def test_write_dead_letter_record_applies_redaction_and_rotation(tmp_path) -> None:
    dead_letter_path = tmp_path / "exit_backfill_dead_letter.jsonl"

    rotated = backfill_exit_columns._write_dead_letter_record(
        str(dead_letter_path),
        {"phase": "velocity", "event_id": "evt-1", "error": "boom", "retries_used": 2},
        max_bytes=1024,
        redact_fields={"event_id"},
    )
    assert rotated is False

    payload_1 = json.loads(dead_letter_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload_1["event_id"] == "[REDACTED]"
    assert payload_1["error"] == "boom"

    rotated = backfill_exit_columns._write_dead_letter_record(
        str(dead_letter_path),
        {"phase": "velocity", "event_id": "evt-2", "error": "boom-2", "retries_used": 1},
        max_bytes=1,
        redact_fields={"event_id", "error"},
    )
    assert rotated is True
    assert (tmp_path / "exit_backfill_dead_letter.jsonl.1").exists()

    payload_2 = json.loads(dead_letter_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload_2["event_id"] == "[REDACTED]"
    assert payload_2["error"] == "[REDACTED]"


def test_write_dead_letter_record_rotates_and_gzips_when_enabled(tmp_path) -> None:
    dead_letter_path = tmp_path / "exit_backfill_dead_letter.jsonl"

    first_rotated = backfill_exit_columns._write_dead_letter_record(
        str(dead_letter_path),
        {"phase": "velocity", "event_id": "evt-1", "error": "boom-1", "retries_used": 0},
        max_bytes=1024,
        redact_fields={"event_id"},
        compress_rotated=True,
    )
    assert first_rotated is False

    second_rotated = backfill_exit_columns._write_dead_letter_record(
        str(dead_letter_path),
        {"phase": "velocity", "event_id": "evt-2", "error": "boom-2", "retries_used": 1},
        max_bytes=1,
        redact_fields={"event_id"},
        compress_rotated=True,
    )
    assert second_rotated is True

    gz_path = tmp_path / "exit_backfill_dead_letter.jsonl.1.gz"
    assert gz_path.exists()
    assert not (tmp_path / "exit_backfill_dead_letter.jsonl.1").exists()

    with gzip.open(gz_path, "rt", encoding="utf-8") as handle:
        rotated_payload = json.loads(handle.read().splitlines()[0])
    assert rotated_payload["event_id"] == "[REDACTED]"
    assert rotated_payload["error"] == "boom-1"


def test_write_dead_letter_record_prunes_oldest_rotation_when_cap_reached(tmp_path) -> None:
    dead_letter_path = tmp_path / "exit_backfill_dead_letter.jsonl"
    dead_letter_path.write_text('{"phase":"seed"}\n', encoding="utf-8")
    (tmp_path / "exit_backfill_dead_letter.jsonl.1").write_text('{"phase":"old-1"}\n', encoding="utf-8")
    (tmp_path / "exit_backfill_dead_letter.jsonl.2").write_text('{"phase":"old-2"}\n', encoding="utf-8")

    rotated = backfill_exit_columns._write_dead_letter_record(
        str(dead_letter_path),
        {"phase": "velocity", "event_id": "evt-3", "error": "boom-3", "retries_used": 0},
        max_bytes=1,
        max_rotated_files=2,
    )
    assert rotated is True
    assert not (tmp_path / "exit_backfill_dead_letter.jsonl.1").exists()

    rotated_files = sorted(tmp_path.glob("exit_backfill_dead_letter.jsonl.[0-9]*"))
    assert len(rotated_files) == 2
    assert {path.name for path in rotated_files} == {
        "exit_backfill_dead_letter.jsonl.2",
        "exit_backfill_dead_letter.jsonl.3",
    }


def test_write_dead_letter_record_prunes_oldest_gzip_rotation_when_cap_reached(tmp_path) -> None:
    dead_letter_path = tmp_path / "exit_backfill_dead_letter.jsonl"
    dead_letter_path.write_text('{"phase":"seed"}\n', encoding="utf-8")
    with gzip.open(tmp_path / "exit_backfill_dead_letter.jsonl.1.gz", "wt", encoding="utf-8") as handle:
        handle.write('{"phase":"old-1"}\n')
    with gzip.open(tmp_path / "exit_backfill_dead_letter.jsonl.2.gz", "wt", encoding="utf-8") as handle:
        handle.write('{"phase":"old-2"}\n')

    rotated = backfill_exit_columns._write_dead_letter_record(
        str(dead_letter_path),
        {"phase": "velocity", "event_id": "evt-4", "error": "boom-4", "retries_used": 0},
        max_bytes=1,
        max_rotated_files=2,
        compress_rotated=True,
    )
    assert rotated is True
    assert not (tmp_path / "exit_backfill_dead_letter.jsonl.1.gz").exists()

    rotated_files = sorted(tmp_path.glob("exit_backfill_dead_letter.jsonl.*.gz"))
    assert len(rotated_files) == 2
    assert {path.name for path in rotated_files} == {
        "exit_backfill_dead_letter.jsonl.2.gz",
        "exit_backfill_dead_letter.jsonl.3.gz",
    }


@pytest.mark.asyncio
async def test_run_backfill_dead_letter_redaction_and_rotation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    dead_letter_path = tmp_path / "exit_backfill_dead_letter.jsonl"
    ts1 = datetime(2026, 2, 9, 14, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 2, 9, 14, 5, tzinfo=timezone.utc)

    async def _fake_get_records_to_backfill(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if after_entry_ts is None:
            return [
                {"event_id": "vel-fail-1", "entry_ts": ts1},
                {"event_id": "vel-fail-2", "entry_ts": ts2},
            ]
        return []

    async def _fake_get_all_records_for_checkpoints(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return []

    async def _always_fail_velocity(_record: dict[str, Any]) -> bool:
        raise RuntimeError("secret-error")

    async def _fake_update_checkpoint_columns(_record: dict[str, Any]) -> bool:
        return True

    async def _fake_init_db() -> None:
        return None

    async def _fake_load_velocity_backfill_cursor() -> tuple[datetime | None, str | None]:
        return None, None

    async def _fake_load_checkpoint_backfill_cursor() -> tuple[datetime | None, str | None]:
        return None, None

    async def _fake_save_velocity_backfill_cursor(_entry_ts: datetime, _event_id: str | None) -> None:
        return None

    async def _fake_save_checkpoint_backfill_cursor(_entry_ts: datetime, _event_id: str | None) -> None:
        return None

    async def _fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(backfill_exit_columns, "get_records_to_backfill", _fake_get_records_to_backfill)
    monkeypatch.setattr(backfill_exit_columns, "get_all_records_for_checkpoints", _fake_get_all_records_for_checkpoints)
    monkeypatch.setattr(backfill_exit_columns, "update_velocity_columns", _always_fail_velocity)
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
    monkeypatch.setattr(backfill_exit_columns.asyncio, "sleep", _fake_sleep)

    summary = await backfill_exit_columns.run_backfill(
        batch_size=2,
        limit=2,
        max_retries=0,
        dead_letter_path=str(dead_letter_path),
        dead_letter_max_bytes=1,
        dead_letter_redact_fields={"event_id", "error"},
    )

    assert summary["velocity"]["failed"] == 2
    assert summary["total_dead_lettered"] == 2
    assert summary["total_dead_letter_rotated"] >= 1
    assert (tmp_path / "exit_backfill_dead_letter.jsonl.1").exists()

    payloads: list[dict[str, Any]] = []
    for file_path in sorted(tmp_path.glob("exit_backfill_dead_letter.jsonl*")):
        for line in file_path.read_text(encoding="utf-8").splitlines():
            payloads.append(json.loads(line))
    assert len(payloads) == 2
    assert all(payload["event_id"] == "[REDACTED]" for payload in payloads)
    assert all(payload["error"] == "[REDACTED]" for payload in payloads)


@pytest.mark.asyncio
async def test_run_backfill_dead_letter_rotation_tracks_compressed_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    dead_letter_path = tmp_path / "exit_backfill_dead_letter.jsonl"
    ts1 = datetime(2026, 2, 9, 14, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 2, 9, 14, 5, tzinfo=timezone.utc)

    async def _fake_get_records_to_backfill(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if after_entry_ts is None:
            return [
                {"event_id": "vel-fail-1", "entry_ts": ts1},
                {"event_id": "vel-fail-2", "entry_ts": ts2},
            ]
        return []

    async def _fake_get_all_records_for_checkpoints(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return []

    async def _always_fail_velocity(_record: dict[str, Any]) -> bool:
        raise RuntimeError("secret-error")

    async def _fake_update_checkpoint_columns(_record: dict[str, Any]) -> bool:
        return True

    async def _fake_init_db() -> None:
        return None

    async def _fake_load_velocity_backfill_cursor() -> tuple[datetime | None, str | None]:
        return None, None

    async def _fake_load_checkpoint_backfill_cursor() -> tuple[datetime | None, str | None]:
        return None, None

    async def _fake_save_velocity_backfill_cursor(_entry_ts: datetime, _event_id: str | None) -> None:
        return None

    async def _fake_save_checkpoint_backfill_cursor(_entry_ts: datetime, _event_id: str | None) -> None:
        return None

    async def _fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(backfill_exit_columns, "get_records_to_backfill", _fake_get_records_to_backfill)
    monkeypatch.setattr(backfill_exit_columns, "get_all_records_for_checkpoints", _fake_get_all_records_for_checkpoints)
    monkeypatch.setattr(backfill_exit_columns, "update_velocity_columns", _always_fail_velocity)
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
    monkeypatch.setattr(backfill_exit_columns.asyncio, "sleep", _fake_sleep)

    summary = await backfill_exit_columns.run_backfill(
        batch_size=2,
        limit=2,
        max_retries=0,
        dead_letter_path=str(dead_letter_path),
        dead_letter_max_bytes=1,
        dead_letter_redact_fields={"event_id", "error"},
        dead_letter_compress_rotated=True,
    )

    assert summary["velocity"]["failed"] == 2
    assert summary["total_dead_lettered"] == 2
    assert summary["total_dead_letter_rotated"] >= 1
    assert summary["total_dead_letter_compressed"] >= 1
    assert summary["dead_letter_compress_rotated"] is True
    assert (tmp_path / "exit_backfill_dead_letter.jsonl.1.gz").exists()


@pytest.mark.asyncio
async def test_run_backfill_aborts_when_max_failed_records_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ts1 = datetime(2026, 2, 9, 14, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 2, 9, 14, 5, tzinfo=timezone.utc)
    checkpoint_calls = {"count": 0}

    async def _fake_get_records_to_backfill(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if after_entry_ts is None:
            return [
                {"event_id": "vel-fail-1", "entry_ts": ts1},
                {"event_id": "vel-fail-2", "entry_ts": ts2},
            ]
        return []

    async def _fake_get_all_records_for_checkpoints(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        checkpoint_calls["count"] += 1
        return []

    async def _always_fail_velocity(_record: dict[str, Any]) -> bool:
        raise RuntimeError("velocity failure")

    async def _fake_update_checkpoint_columns(_record: dict[str, Any]) -> bool:
        return True

    async def _fake_init_db() -> None:
        return None

    async def _fake_load_velocity_backfill_cursor() -> tuple[datetime | None, str | None]:
        return None, None

    async def _fake_load_checkpoint_backfill_cursor() -> tuple[datetime | None, str | None]:
        return None, None

    async def _fake_save_velocity_backfill_cursor(_entry_ts: datetime, _event_id: str | None) -> None:
        return None

    async def _fake_save_checkpoint_backfill_cursor(_entry_ts: datetime, _event_id: str | None) -> None:
        return None

    async def _fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(backfill_exit_columns, "get_records_to_backfill", _fake_get_records_to_backfill)
    monkeypatch.setattr(backfill_exit_columns, "get_all_records_for_checkpoints", _fake_get_all_records_for_checkpoints)
    monkeypatch.setattr(backfill_exit_columns, "update_velocity_columns", _always_fail_velocity)
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
    monkeypatch.setattr(backfill_exit_columns.asyncio, "sleep", _fake_sleep)

    summary = await backfill_exit_columns.run_backfill(
        batch_size=2,
        limit=2,
        max_retries=0,
        max_failed_records=1,
    )

    assert summary["aborted"] is True
    assert summary["abort_reason"] == "max_failed_records_reached"
    assert summary["max_failed_records"] == 1
    assert summary["velocity"]["failed"] == 1
    assert summary["checkpoint"]["processed"] == 0
    assert checkpoint_calls["count"] == 0


@pytest.mark.asyncio
async def test_run_backfill_summary_includes_elapsed_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ts1 = datetime(2026, 2, 9, 14, 0, tzinfo=timezone.utc)

    async def _fake_get_records_to_backfill(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if after_entry_ts is None:
            return [{"event_id": "vel-1", "entry_ts": ts1}]
        return []

    async def _fake_get_all_records_for_checkpoints(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if after_entry_ts is None:
            return [{"event_id": "cp-1", "entry_ts": ts1, "option_chain": "AAPL", "entry_option_price": 1.0}]
        return []

    async def _fake_update_velocity_columns(_record: dict[str, Any]) -> bool:
        return True

    async def _fake_update_checkpoint_columns(_record: dict[str, Any]) -> bool:
        return True

    async def _fake_init_db() -> None:
        return None

    async def _fake_load_velocity_backfill_cursor() -> tuple[datetime | None, str | None]:
        return None, None

    async def _fake_load_checkpoint_backfill_cursor() -> tuple[datetime | None, str | None]:
        return None, None

    async def _fake_save_velocity_backfill_cursor(_entry_ts: datetime, _event_id: str | None) -> None:
        return None

    async def _fake_save_checkpoint_backfill_cursor(_entry_ts: datetime, _event_id: str | None) -> None:
        return None

    clock_values = itertools.chain([10.0, 11.5, 13.0, 14.0, 14.5, 15.0], itertools.repeat(15.0))
    monkeypatch.setattr(backfill_exit_columns.time, "perf_counter", lambda: next(clock_values), raising=False)

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

    summary = await backfill_exit_columns.run_backfill(batch_size=1, limit=1)

    assert summary["velocity"]["elapsed_seconds"] == pytest.approx(1.5)
    assert summary["checkpoint"]["elapsed_seconds"] == pytest.approx(0.5)
    assert summary["total_elapsed_seconds"] == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_run_backfill_aborts_when_max_duration_seconds_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ts1 = datetime(2026, 2, 9, 14, 0, tzinfo=timezone.utc)

    async def _fake_get_records_to_backfill(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if after_entry_ts is None:
            return [{"event_id": "vel-1", "entry_ts": ts1}]
        return []

    async def _fake_get_all_records_for_checkpoints(
        limit: int,
        after_entry_ts: datetime | None = None,
        after_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return []

    async def _fake_update_velocity_columns(_record: dict[str, Any]) -> bool:
        return True

    async def _fake_update_checkpoint_columns(_record: dict[str, Any]) -> bool:
        return True

    async def _fake_init_db() -> None:
        return None

    async def _fake_load_velocity_backfill_cursor() -> tuple[datetime | None, str | None]:
        return None, None

    async def _fake_load_checkpoint_backfill_cursor() -> tuple[datetime | None, str | None]:
        return None, None

    async def _fake_save_velocity_backfill_cursor(_entry_ts: datetime, _event_id: str | None) -> None:
        return None

    async def _fake_save_checkpoint_backfill_cursor(_entry_ts: datetime, _event_id: str | None) -> None:
        return None

    clock_values = itertools.chain([0.0, 0.0, 0.0, 0.0, 2.0, 2.0], itertools.repeat(2.0))
    monkeypatch.setattr(backfill_exit_columns.time, "perf_counter", lambda: next(clock_values), raising=False)

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

    summary = await backfill_exit_columns.run_backfill(
        batch_size=1,
        limit=10,
        max_duration_seconds=1.0,
    )

    assert summary["aborted"] is True
    assert summary["abort_reason"] == "max_duration_seconds_reached"
    assert summary["max_duration_seconds"] == pytest.approx(1.0)
