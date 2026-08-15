"""Tests for the per-service single-instance lease.

H-05 from predict 260430-1751-execution-reliability flagged that the
single-process invariant — load-bearing for `pending_orders`,
`processed_fill_ids`, `_partial_fill_tracker`, `_closing_symbols` — was
undocumented and unenforced. The fix adds a soft lease backed by SystemStatus.
Each `main_*.py` calls `engine.acquire_service_lease("<service-id>")` before
init; renewal is automatic via `poll_fills`.

These tests pin:
  - First acquisition succeeds
  - Concurrent fresh lease from another run_id refuses to start
  - Stale lease (>120s) is treated as crashed-prior-run and overwritten
  - Renewal updates last_updated_utc and continues to identify as same run_id
  - Renewal is a no-op when no lease was acquired
  - Lease-lost (another process took over) is logged but does not raise
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

# Force in-memory SQLite for unit tests
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///:memory:")

from sqlalchemy import select

from orion.execution.execution_engine import (
    SERVICE_LEASE_KEY_PREFIX,
    SERVICE_LEASE_STALE_SECONDS,
    ExecutionEngine,
)
from orion.storage.db import async_session_factory, init_db
from orion.storage.models import SystemStatus


async def _reset_lease_row(service_id: str) -> None:
    """Remove any pre-existing lease row so tests start clean."""
    key = f"{SERVICE_LEASE_KEY_PREFIX}{service_id}"
    async with async_session_factory() as session:
        stmt = select(SystemStatus).where(SystemStatus.key == key)
        existing = (await session.execute(stmt)).scalars().first()
        if existing:
            await session.delete(existing)
            await session.commit()


@pytest.mark.integration
@pytest.mark.asyncio
class TestAcquireServiceLease:
    async def test_first_acquisition_succeeds_and_persists_row(self) -> None:
        await init_db()
        await _reset_lease_row("test_service")

        engine = ExecutionEngine()
        await engine.acquire_service_lease("test_service")

        assert engine._lease_service_id == "test_service"
        assert engine._lease_run_id is not None

        async with async_session_factory() as session:
            stmt = select(SystemStatus).where(SystemStatus.key == f"{SERVICE_LEASE_KEY_PREFIX}test_service")
            row = (await session.execute(stmt)).scalars().first()

        assert row is not None
        assert row.status == "RUNNING"
        assert f"run_id={engine._lease_run_id}" in row.details

    async def test_concurrent_fresh_lease_from_another_runner_refuses(self) -> None:
        await init_db()
        await _reset_lease_row("test_service2")

        # First instance acquires
        first = ExecutionEngine()
        await first.acquire_service_lease("test_service2")

        # Second instance — different run_id (new ExecutionEngine = new uuid) —
        # must refuse because the first lease is fresh (just claimed).
        second = ExecutionEngine()
        with pytest.raises(RuntimeError, match="Another 'test_service2' instance holds a fresh lease"):
            await second.acquire_service_lease("test_service2")

        # Second engine's lease state stays unset
        assert second._lease_service_id is None
        assert second._lease_run_id is None

    async def test_stale_lease_is_overwritten(self) -> None:
        await init_db()
        await _reset_lease_row("test_service3")

        # Manually plant a stale lease (last_updated > SERVICE_LEASE_STALE_SECONDS ago)
        stale_age = SERVICE_LEASE_STALE_SECONDS + 10
        async with async_session_factory() as session:
            session.add(
                SystemStatus(
                    key=f"{SERVICE_LEASE_KEY_PREFIX}test_service3",
                    status="RUNNING",
                    details="run_id=00000000-0000-0000-0000-000000000000 host=ghost pid=1",
                    last_updated_utc=datetime.now(UTC) - timedelta(seconds=stale_age),
                )
            )
            await session.commit()

        engine = ExecutionEngine()
        await engine.acquire_service_lease("test_service3")

        # New lease takes over — row's details now reflects engine's run_id
        async with async_session_factory() as session:
            stmt = select(SystemStatus).where(SystemStatus.key == f"{SERVICE_LEASE_KEY_PREFIX}test_service3")
            row = (await session.execute(stmt)).scalars().first()

        assert f"run_id={engine._lease_run_id}" in row.details
        assert "ghost" not in row.details

    async def test_different_service_ids_can_coexist(self) -> None:
        """`main_execution` and `main_position_monitor` use different service_ids
        and must both be allowed to acquire leases simultaneously."""
        await init_db()
        await _reset_lease_row("svc_a")
        await _reset_lease_row("svc_b")

        a = ExecutionEngine()
        await a.acquire_service_lease("svc_a")

        b = ExecutionEngine()
        await b.acquire_service_lease("svc_b")

        assert a._lease_run_id != b._lease_run_id
        # Both rows exist, neither blocks the other
        async with async_session_factory() as session:
            stmt = select(SystemStatus).where(
                SystemStatus.key.in_([f"{SERVICE_LEASE_KEY_PREFIX}svc_a", f"{SERVICE_LEASE_KEY_PREFIX}svc_b"])
            )
            rows = list((await session.execute(stmt)).scalars().all())
        assert len(rows) == 2


@pytest.mark.integration
@pytest.mark.asyncio
class TestRenewServiceLease:
    async def test_no_op_when_no_lease_acquired(self) -> None:
        engine = ExecutionEngine()
        # Should not raise even though _lease_service_id is None
        await engine.renew_service_lease()

    async def test_renew_updates_last_updated_utc(self) -> None:
        await init_db()
        await _reset_lease_row("renew_test")

        engine = ExecutionEngine()
        await engine.acquire_service_lease("renew_test")

        async with async_session_factory() as session:
            stmt = select(SystemStatus).where(SystemStatus.key == f"{SERVICE_LEASE_KEY_PREFIX}renew_test")
            initial = (await session.execute(stmt)).scalars().first()
        initial_ts = initial.last_updated_utc

        # Wait briefly so timestamps can differ
        import asyncio

        await asyncio.sleep(0.01)
        await engine.renew_service_lease()

        async with async_session_factory() as session:
            stmt = select(SystemStatus).where(SystemStatus.key == f"{SERVICE_LEASE_KEY_PREFIX}renew_test")
            renewed = (await session.execute(stmt)).scalars().first()

        assert renewed.last_updated_utc >= initial_ts
        # Same run_id — renewal does NOT mint a new id
        assert f"run_id={engine._lease_run_id}" in renewed.details

    async def test_renew_aborts_when_another_process_owns_lease(self, caplog) -> None:
        """If another process took over the lease (e.g. we hung past the stale
        threshold), renew must NOT overwrite their row — log and bow out."""
        await init_db()
        await _reset_lease_row("takeover_test")

        engine = ExecutionEngine()
        await engine.acquire_service_lease("takeover_test")

        # Simulate another process taking over by overwriting the row directly
        async with async_session_factory() as session:
            stmt = select(SystemStatus).where(SystemStatus.key == f"{SERVICE_LEASE_KEY_PREFIX}takeover_test")
            row = (await session.execute(stmt)).scalars().first()
            row.details = "run_id=ffffffff-ffff-ffff-ffff-ffffffffffff host=other pid=9999"
            await session.commit()

        # Renew should NOT raise, and must NOT overwrite the other process's row
        await engine.renew_service_lease()

        async with async_session_factory() as session:
            stmt = select(SystemStatus).where(SystemStatus.key == f"{SERVICE_LEASE_KEY_PREFIX}takeover_test")
            row = (await session.execute(stmt)).scalars().first()

        assert "ffffffff" in row.details  # Other process's id preserved
        assert engine._lease_run_id not in row.details

    async def test_renew_swallows_db_errors(self) -> None:
        """A transient DB failure during renewal must not crash the main loop."""
        from unittest.mock import MagicMock, patch

        engine = ExecutionEngine()
        engine._lease_service_id = "phantom"
        engine._lease_run_id = "phantom-run"

        # Lease logic now lives in orion.core.service_lease; ExecutionEngine
        # delegates. Patch the session factory at its actual call site.
        fake_factory = MagicMock()
        fake_factory.return_value.__aenter__.side_effect = RuntimeError("simulated DB failure")
        with patch("orion.core.service_lease.async_session_factory", fake_factory):
            # Should not raise
            await engine.renew_service_lease()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_poll_fills_renews_lease() -> None:
    """The main execution loop's poll_fills cycle must keep the lease fresh."""
    await init_db()
    await _reset_lease_row("poll_renew")

    engine = ExecutionEngine()
    await engine.acquire_service_lease("poll_renew")

    # Manipulate the row to simulate aging
    async with async_session_factory() as session:
        stmt = select(SystemStatus).where(SystemStatus.key == f"{SERVICE_LEASE_KEY_PREFIX}poll_renew")
        row = (await session.execute(stmt)).scalars().first()
        old_ts = datetime.now(UTC) - timedelta(seconds=60)
        row.last_updated_utc = old_ts
        await session.commit()

    # poll_fills with no Gateway available short-circuits but still renews
    engine._gateway_available = False
    await engine.poll_fills()

    async with async_session_factory() as session:
        stmt = select(SystemStatus).where(SystemStatus.key == f"{SERVICE_LEASE_KEY_PREFIX}poll_renew")
        row = (await session.execute(stmt)).scalars().first()

    # last_updated_utc must have advanced past the planted old_ts. SQLite
    # readback returns naive datetimes; coerce both sides to UTC-aware.
    from orion.shared.utils import ensure_utc

    assert ensure_utc(row.last_updated_utc) > ensure_utc(old_ts)
