from __future__ import annotations

from typing import Any

import pytest

from orion.jobs import cleanup_legacy_backfill_watermarks as cleanup_job


def test_legacy_backfill_watermark_keys_include_cursor_suffixes() -> None:
    assert "backfill_exit_columns.velocity.cursor" in cleanup_job.LEGACY_BACKFILL_WATERMARK_KEYS
    assert "backfill_exit_columns.checkpoint.cursor" in cleanup_job.LEGACY_BACKFILL_WATERMARK_KEYS
    assert "backfill_ml_features.price_target_labels.cursor" not in cleanup_job.LEGACY_BACKFILL_WATERMARK_KEYS


@pytest.mark.asyncio
async def test_cleanup_legacy_backfill_watermarks_deletes_known_legacy_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted_keys: list[tuple[str, ...]] = []

    async def _fake_delete_watermarks(_session: Any, keys: tuple[str, ...]) -> int:
        deleted_keys.append(tuple(keys))
        return 3

    async def _fake_db_write(fn):
        return await fn(object())

    monkeypatch.setattr(cleanup_job, "delete_watermarks", _fake_delete_watermarks)
    monkeypatch.setattr(cleanup_job, "db_write", _fake_db_write)

    deleted = await cleanup_job.cleanup_legacy_backfill_watermarks(dry_run=False)

    assert deleted == 3
    assert deleted_keys == [cleanup_job.LEGACY_BACKFILL_WATERMARK_KEYS]


@pytest.mark.asyncio
async def test_cleanup_legacy_backfill_watermarks_dry_run_counts_without_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_count(_session: Any) -> int:
        return 2

    async def _fake_delete_watermarks(_session: Any, _keys: tuple[str, ...]) -> int:
        raise AssertionError("dry-run should not delete watermarks")

    async def _fake_db_query(fn):
        return await fn(object())

    monkeypatch.setattr(cleanup_job, "_count_legacy_backfill_watermarks", _fake_count)
    monkeypatch.setattr(cleanup_job, "delete_watermarks", _fake_delete_watermarks)
    monkeypatch.setattr(cleanup_job, "db_query", _fake_db_query)

    count = await cleanup_job.cleanup_legacy_backfill_watermarks(dry_run=True)
    assert count == 2
