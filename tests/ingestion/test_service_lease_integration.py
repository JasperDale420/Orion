"""Tests that IngestionService actually uses the single-instance lease.

Phase 5b's docker-compose `profiles: [docker]` gate keeps the
operator from accidentally running both the native launchd process
AND the compose container, but if both DO start (forgotten profile,
manual override, etc.) the database-level guard in
`orion.core.service_lease` MUST cause one of them to refuse start.

These tests pin:
  1. A fresh IngestionService acquires the "ingestion" lease during
     initialize() and stashes the run_id.
  2. A second IngestionService with a different lease owner refuses
     to start with the documented RuntimeError, preserving the
     single-process invariant.
  3. The run() heartbeat block calls renew_service_lease so a long-
     lived process keeps its lease fresh.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

# Force in-memory SQLite before any orion imports
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///:memory:")

from sqlalchemy import select  # noqa: E402

from orion.core.service_lease import (  # noqa: E402
    SERVICE_LEASE_KEY_PREFIX,
    acquire_service_lease,
)
from orion.storage.db import async_session_factory, init_db  # noqa: E402
from orion.storage.models import SystemStatus  # noqa: E402


async def _reset_lease_row(service_id: str) -> None:
    key = f"{SERVICE_LEASE_KEY_PREFIX}{service_id}"
    async with async_session_factory() as session:
        stmt = select(SystemStatus).where(SystemStatus.key == key)
        existing = (await session.execute(stmt)).scalars().first()
        if existing:
            await session.delete(existing)
            await session.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_first_ingestion_acquires_and_second_refuses(monkeypatch) -> None:
    await init_db()
    await _reset_lease_row("ingestion")

    # Pretend we are the compose process — stable owner id.
    monkeypatch.setenv("ORION_LEASE_OWNER_ID", "orion_ingestion_compose_test")

    first_run_id = await acquire_service_lease("ingestion")
    assert first_run_id == "orion_ingestion_compose_test"

    # Now pretend a second process — the native one — starts WITHOUT
    # respecting the docker-compose profile gate. It uses a distinct
    # owner id so the lease guard MUST refuse it.
    monkeypatch.setenv("ORION_LEASE_OWNER_ID", "orion_ingestion_native_test")

    with pytest.raises(RuntimeError, match="Another 'ingestion' instance holds a fresh lease"):
        await acquire_service_lease("ingestion")


def _build_service_with_mocks(monkeypatch):
    """Construct IngestionService with the network-touching deps stubbed.

    `IngestionService.__init__` calls `create_gateway_stream_client()`
    which validates `system_settings.data_gateway_api_key` (cached at
    pydantic-Settings load time, so a late env-var set won't help).
    Patch the factory itself with an AsyncMock-ish object.
    """
    from unittest.mock import MagicMock

    fake_stream = MagicMock()
    fake_stream.is_running = False
    fake_stream.subscribed_symbols = set()
    fake_stream.start = AsyncMock()
    fake_stream.stop = AsyncMock()
    fake_stream.subscribe = AsyncMock()
    fake_stream.drain_events = MagicMock(return_value=[])

    monkeypatch.setattr(
        "orion.ingestion.service.create_gateway_stream_client",
        lambda: fake_stream,
    )

    from orion.ingestion.service import IngestionService

    return IngestionService()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingestion_initialize_acquires_lease(monkeypatch) -> None:
    """Calling IngestionService.initialize() must result in a row
    in `system_status` keyed `service_lease_ingestion`."""
    await init_db()
    await _reset_lease_row("ingestion")
    monkeypatch.setenv("ORION_LEASE_OWNER_ID", "orion_ingestion_init_test")

    service = _build_service_with_mocks(monkeypatch)

    # Stub everything in initialize() that needs network or filesystem.
    # The lease acquisition happens first and is the only thing we want
    # to exercise here.
    with (
        patch.object(service.universe, "hydrate_from_db", new=AsyncMock()),
        patch.object(service.feature_engine, "hydrate_history", new=AsyncMock()),
        patch.object(service.gateway_stream, "start", new=AsyncMock()),
        patch.object(service.gateway_stream, "subscribe", new=AsyncMock()),
        patch("orion.jobs.rollup_job.RollupJob") as fake_rollup_cls,
    ):
        fake_rollup_cls.return_value.run_forever = AsyncMock()
        await service.initialize()

    assert service._lease_run_id == "orion_ingestion_init_test"

    async with async_session_factory() as session:
        stmt = select(SystemStatus).where(SystemStatus.key == f"{SERVICE_LEASE_KEY_PREFIX}ingestion")
        row = (await session.execute(stmt)).scalars().first()

    assert row is not None
    assert row.status == "RUNNING"
    assert f"run_id={service._lease_run_id}" in row.details


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingestion_heartbeat_renews_lease(monkeypatch) -> None:
    """The heartbeat path in run() must call renew_service_lease."""
    service = _build_service_with_mocks(monkeypatch)
    service._lease_run_id = "ghost-run"

    with patch("orion.ingestion.service.renew_service_lease", new=AsyncMock()) as spy:
        await service._maybe_renew_lease()
        spy.assert_awaited_once_with("ingestion", "ghost-run", fence_on_confirmed_loss=True)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_overnight_sleep_renews_lease_each_chunk(monkeypatch) -> None:
    """The market-closed sleep must keep the lease fresh, not just the heartbeat.

    `_check_overnight_sleep` blocks the whole cycle until the next open, so the
    renewal in `run()`'s loop tail is unreachable all weekend. Observed
    2026-08-14: `service_lease_ingestion` froze at Friday close while
    `global_health` stayed fresh, voiding the split-brain guard for ~60 hours
    (SERVICE_LEASE_STALE_SECONDS is 120). Every 60s sleep chunk must renew.
    """
    from unittest.mock import MagicMock

    service = _build_service_with_mocks(monkeypatch)
    service._lease_run_id = "sleeping-run"

    schedule = MagicMock()
    schedule.is_market_open.return_value = False
    schedule.seconds_until_open.return_value = 120.0  # two 60s chunks

    with (
        patch("orion.core.market_schedule.MarketSchedule", return_value=schedule),
        patch("orion.ingestion.service.asyncio.sleep", new=AsyncMock()),
        patch.object(service, "_update_health_status", new=AsyncMock()),
        patch.object(service, "_maybe_run_eod", new=AsyncMock()),
        patch.object(service, "_maybe_renew_lease", new=AsyncMock()) as renew,
    ):
        await service._check_overnight_sleep()

    assert renew.await_count >= 2, "lease must be renewed on every overnight sleep chunk"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ingestion_heartbeat_no_op_without_lease(monkeypatch) -> None:
    """If initialize() never acquired a lease (None run_id), the
    heartbeat helper must not call the renewer (avoids polluting the
    SystemStatus row with a phantom owner)."""
    service = _build_service_with_mocks(monkeypatch)
    service._lease_run_id = None

    with patch("orion.ingestion.service.renew_service_lease", new=AsyncMock()) as spy:
        await service._maybe_renew_lease()
        spy.assert_not_awaited()
