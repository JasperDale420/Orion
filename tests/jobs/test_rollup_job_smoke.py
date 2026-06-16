"""Smoke tests for orion.jobs.rollup_job.RollupJob.

Audit #21 coverage. RollupJob.run_once walks its ticker list, reads each
ticker's watermark, calls RollupBuilder.build_rollups, and advances the
watermark when the builder returns a timestamp. We mock RollupBuilder (the
heavy DB-reading dependency) and use the conftest in-memory SQLite DB for the
watermark table to assert the persisted EFFECT.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from orion.jobs.rollup_job import RollupJob
from orion.storage.db import async_session_factory
from orion.storage.watermarks import get_watermark


@pytest.mark.asyncio
async def test_run_once_builds_rollups_for_each_ticker() -> None:
    """Happy path: run_once iterates the ticker list, calls
    RollupBuilder.build_rollups once per ticker, and PERSISTS each ticker's
    watermark (regression: run_once previously never committed, so the
    session rollback on exit discarded every watermark and the job
    cold-started on every run)."""
    latest = datetime(2026, 6, 10, 15, 30, tzinfo=UTC)
    job = RollupJob(tickers=["AAPL", "MSFT"])

    with patch("orion.jobs.rollup_job.RollupBuilder") as MockBuilder:
        MockBuilder.return_value.build_rollups = AsyncMock(return_value=latest)
        await job.run_once()
        # One build call per ticker.
        assert MockBuilder.return_value.build_rollups.await_count == 2

    # Watermarks persist across sessions now that run_once commits.
    async with async_session_factory() as session:
        assert await get_watermark(session, key="rollups:AAPL") == latest
        assert await get_watermark(session, key="rollups:MSFT") == latest


@pytest.mark.asyncio
async def test_run_once_skips_upsert_when_builder_returns_none() -> None:
    """When build_rollups returns None (no data in window), the upsert branch
    is skipped entirely (``continue`` before upsert), so no watermark row is
    created."""
    job = RollupJob(tickers=["AAPL"])

    with patch("orion.jobs.rollup_job.RollupBuilder") as MockBuilder:
        build = AsyncMock(return_value=None)
        MockBuilder.return_value.build_rollups = build
        await job.run_once()
        assert build.await_count == 1

    async with async_session_factory() as session:
        assert await get_watermark(session, key="rollups:AAPL") is None


@pytest.mark.asyncio
async def test_run_once_uses_existing_watermark_as_start_with_overlap() -> None:
    """When a watermark exists, the builder start_time is wm - overlap_seconds,
    proving the job resumes from the cursor rather than cold-starting."""
    from orion.storage.watermarks import upsert_watermark

    existing = datetime(2026, 6, 10, 14, 0, tzinfo=UTC)
    async with async_session_factory() as session:
        await upsert_watermark(session, key="rollups:AAPL", last_seen_ts_utc=existing)
        await session.commit()

    job = RollupJob(tickers=["AAPL"], overlap_seconds=120)

    with patch("orion.jobs.rollup_job.RollupBuilder") as MockBuilder:
        build = AsyncMock(return_value=None)
        MockBuilder.return_value.build_rollups = build
        await job.run_once()

    _, kwargs = build.call_args
    assert kwargs["start_time"] == existing.replace(second=0) - timedelta(seconds=120)


@pytest.mark.asyncio
async def test_builder_error_propagates_out_of_run_once() -> None:
    """Failure path: run_once itself has NO try/except (rollup_job.py lines
    37-55); the resilience boundary is run_forever's loop (lines 66-73), which
    logs and continues. So a builder error must propagate out of run_once -
    document that contract here."""
    job = RollupJob(tickers=["AAPL"])

    with patch("orion.jobs.rollup_job.RollupBuilder") as MockBuilder:
        MockBuilder.return_value.build_rollups = AsyncMock(side_effect=RuntimeError("read failed"))
        with pytest.raises(RuntimeError, match="read failed"):
            await job.run_once()
