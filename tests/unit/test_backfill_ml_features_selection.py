from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import pytest

from orion.jobs import backfill_ml_features


def test_backfill_cursor_key_uses_heber_neutral_name() -> None:
    assert backfill_ml_features.BACKFILL_CURSOR_KEY == "backfill_ml_features.heber_gold.cursor"


@pytest.mark.asyncio
async def test_get_records_to_backfill_uses_deterministic_ordering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeReader:
        def read_gold_features(self, dataset: str, asof_time, symbols=None):
            _ = (asof_time, symbols)
            if dataset == "labels_alert_barriers":
                return pd.DataFrame(
                    {
                        "alert_id": ["evt-2", "evt-1"],
                        "underlying": ["MSFT", "AAPL"],
                        "ts_event": [
                            datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc),
                            datetime(2026, 2, 9, 14, 0, tzinfo=timezone.utc),
                        ],
                        "expiry": ["2026-02-21", "2026-02-21"],
                        "dte": [12, 12],
                    }
                )
            if dataset == "meta_label_features":
                return pd.DataFrame({"alert_id": ["evt-2"], "hour_of_day": [15]})
            raise AssertionError(f"unexpected dataset requested: {dataset}")

    async def _fail_db_query(_fn):
        raise AssertionError("local db_query should not be used for candidate selection")

    monkeypatch.setattr(backfill_ml_features, "get_heber_reader", lambda: _FakeReader())
    monkeypatch.setattr(backfill_ml_features, "db_query", _fail_db_query, raising=False)

    records = await backfill_ml_features.get_records_to_backfill(limit=25)

    assert len(records) == 2
    assert [r["event_id"] for r in records] == ["evt-1", "evt-2"]
    assert records[0]["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_get_option_chain_for_event_prefers_heber(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    flow_df = pd.DataFrame(
        {
            "event_id": ["evt-1", "evt-2"],
            "option_chain": ["AAPL260220C00200000", "AAPL260220P00190000"],
            "ts_event": [now - timedelta(minutes=3), now - timedelta(minutes=1)],
        }
    )

    class _FakeReader:
        def read_flow(self, **_kwargs):
            return flow_df

    monkeypatch.setattr(backfill_ml_features, "get_heber_reader", lambda: _FakeReader())

    option_chain = await backfill_ml_features._get_option_chain_for_event("evt-2")

    assert option_chain == "AAPL260220P00190000"


@pytest.mark.asyncio
async def test_get_records_to_backfill_supports_cursor_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeReader:
        def read_gold_features(self, dataset: str, asof_time, symbols=None):
            _ = (asof_time, symbols)
            if dataset == "labels_alert_barriers":
                return pd.DataFrame(
                    {
                        "alert_id": ["evt-100", "evt-101", "evt-102"],
                        "underlying": ["AAPL", "MSFT", "TSLA"],
                        "ts_event": [
                            datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc),
                            datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc),
                            datetime(2026, 2, 9, 16, 0, tzinfo=timezone.utc),
                        ],
                    }
                )
            if dataset == "meta_label_features":
                return pd.DataFrame()
            raise AssertionError(f"unexpected dataset requested: {dataset}")

    monkeypatch.setattr(backfill_ml_features, "get_heber_reader", lambda: _FakeReader())

    cursor_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    records = await backfill_ml_features.get_records_to_backfill(
        limit=10,
        after_entry_ts=cursor_ts,
        after_event_id="evt-100",
    )

    assert [r["event_id"] for r in records] == ["evt-101", "evt-102"]


@pytest.mark.asyncio
async def test_get_records_to_backfill_supports_timestamp_only_cursor_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeReader:
        def read_gold_features(self, dataset: str, asof_time, symbols=None):
            _ = (asof_time, symbols)
            if dataset == "labels_alert_barriers":
                return pd.DataFrame(
                    {
                        "alert_id": ["evt-099", "evt-100", "evt-101"],
                        "underlying": ["AAPL", "MSFT", "TSLA"],
                        "ts_event": [
                            datetime(2026, 2, 9, 14, 0, tzinfo=timezone.utc),
                            datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc),
                            datetime(2026, 2, 9, 16, 0, tzinfo=timezone.utc),
                        ],
                    }
                )
            if dataset == "meta_label_features":
                return pd.DataFrame()
            raise AssertionError(f"unexpected dataset requested: {dataset}")

    monkeypatch.setattr(backfill_ml_features, "get_heber_reader", lambda: _FakeReader())

    cursor_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    records = await backfill_ml_features.get_records_to_backfill(
        limit=10,
        after_entry_ts=cursor_ts,
        after_event_id=None,
    )

    assert [r["event_id"] for r in records] == ["evt-100", "evt-101"]


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


@pytest.mark.asyncio
async def test_load_backfill_cursor_does_not_fallback_to_legacy_watermark(
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

    monkeypatch.setattr(backfill_ml_features, "get_cursor_state", _fake_get_cursor_state)
    monkeypatch.setattr(backfill_ml_features, "get_watermark", _fake_get_watermark, raising=False)
    monkeypatch.setattr(backfill_ml_features, "db_query", _fake_db_query)

    loaded = await backfill_ml_features._load_backfill_cursor()
    assert loaded == (None, None)


@pytest.mark.asyncio
async def test_load_backfill_cursor_only_checks_canonical_heber_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_keys: list[str] = []

    async def _fake_get_cursor_state(_session: Any, key: str):
        requested_keys.append(key)
        return None

    class _FakeSession:
        pass

    async def _fake_db_query(fn):
        return await fn(_FakeSession())

    monkeypatch.setattr(backfill_ml_features, "get_cursor_state", _fake_get_cursor_state)
    monkeypatch.setattr(backfill_ml_features, "db_query", _fake_db_query)

    loaded = await backfill_ml_features._load_backfill_cursor()

    assert loaded == (None, None)
    assert requested_keys == [backfill_ml_features.BACKFILL_CURSOR_KEY]


@pytest.mark.asyncio
async def test_save_backfill_cursor_does_not_write_legacy_watermark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[tuple[datetime, str | None]] = []

    async def _fake_upsert_cursor_state(
        _session: Any,
        key: str,
        last_seen_ts_utc: datetime,
        last_seen_id: str | None,
    ) -> None:
        assert key == backfill_ml_features.BACKFILL_CURSOR_KEY
        writes.append((last_seen_ts_utc, last_seen_id))

    async def _fake_upsert_watermark(_session: Any, _key: str, _last_seen_ts_utc: datetime) -> None:
        raise AssertionError("legacy watermark fallback should not be written")

    class _FakeSession:
        pass

    async def _fake_db_write(fn):
        return await fn(_FakeSession())

    ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(backfill_ml_features, "upsert_cursor_state", _fake_upsert_cursor_state)
    monkeypatch.setattr(backfill_ml_features, "upsert_watermark", _fake_upsert_watermark, raising=False)
    monkeypatch.setattr(backfill_ml_features, "db_write", _fake_db_write)

    await backfill_ml_features._save_backfill_cursor(ts, "evt-500")
    assert writes == [(ts, "evt-500")]
