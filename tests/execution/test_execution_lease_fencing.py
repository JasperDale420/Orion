"""Execution must stop when its single-instance lease is confirmably taken.

`ExecutionEngine` renews the `execution` lease from `poll_fills`, once per
loop iteration. Its in-memory state — pending_orders, processed_fill_ids,
_partial_fill_tracker, _closing_symbols — is the whole reason the lease exists,
so a displaced instance that keeps polling fills and submitting orders is the
exact failure the lease was built to prevent.

The main loop catches broad exceptions and continues, so the fence has to be
handled ahead of that handler or it would be logged and ignored.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from orion import main_execution
from orion.core.service_lease import SERVICE_LEASE_KEY_PREFIX, ServiceLeaseLostError
from orion.execution.execution_engine import ExecutionEngine
from orion.storage.db import async_session_factory, init_db
from orion.storage.models import SystemStatus

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_engine_renewal_is_fatal_on_a_confirmed_takeover() -> None:
    await init_db()
    engine = ExecutionEngine()
    await engine.acquire_service_lease("fence_exec_takeover")

    key = f"{SERVICE_LEASE_KEY_PREFIX}fence_exec_takeover"
    async with async_session_factory() as session:
        row = (await session.execute(select(SystemStatus).where(SystemStatus.key == key))).scalars().first()
        row.details = "run_id=ffffffff-ffff-ffff-ffff-ffffffffffff host=other pid=9999"
        row.last_updated_utc = datetime.now(UTC)
        await session.commit()

    with pytest.raises(ServiceLeaseLostError):
        await engine.renew_service_lease()

    # The winner's row is untouched.
    async with async_session_factory() as session:
        row = (await session.execute(select(SystemStatus).where(SystemStatus.key == key))).scalars().first()
    assert "ffffffff" in row.details


async def test_engine_renewal_is_a_no_op_without_a_lease() -> None:
    """The position monitor builds an engine but never acquires the execution
    lease; renewal there must stay a no-op rather than start fencing."""
    engine = ExecutionEngine()
    await engine.renew_service_lease()


async def test_a_stale_lease_of_our_own_is_renewed_not_fenced() -> None:
    await init_db()
    engine = ExecutionEngine()
    await engine.acquire_service_lease("fence_exec_own_stale")

    key = f"{SERVICE_LEASE_KEY_PREFIX}fence_exec_own_stale"
    async with async_session_factory() as session:
        row = (await session.execute(select(SystemStatus).where(SystemStatus.key == key))).scalars().first()
        row.last_updated_utc = datetime.now(UTC) - timedelta(seconds=600)
        await session.commit()

    await engine.renew_service_lease()

    async with async_session_factory() as session:
        row = (await session.execute(select(SystemStatus).where(SystemStatus.key == key))).scalars().first()
    assert f"run_id={engine._lease_run_id}" in row.details


async def test_execution_loop_exits_non_zero_on_a_fenced_lease(monkeypatch) -> None:
    shutdown = asyncio.Event()

    async def startup_liveness(_shutdown_event: asyncio.Event) -> None:
        await asyncio.Event().wait()

    execution_engine = MagicMock()
    execution_engine.acquire_service_lease = AsyncMock()
    execution_engine.initialize = AsyncMock()
    execution_engine.poll_fills = AsyncMock(side_effect=ServiceLeaseLostError("taken"))
    execution_engine.gateway_positions_snapshot = {}

    signal_engine = MagicMock()
    signal_engine.initialize = AsyncMock()

    position_manager = MagicMock()
    position_manager.initialize = AsyncMock()
    position_manager.get_open_positions.return_value = []

    monkeypatch.setattr(main_execution, "wait_for_db", AsyncMock())
    monkeypatch.setattr(main_execution, "init_db", AsyncMock())
    monkeypatch.setattr(main_execution, "reconcile_orphaned_decisions", AsyncMock())
    monkeypatch.setattr(
        main_execution,
        "ensure_active_solvers_ready",
        AsyncMock(return_value=SimpleNamespace(active_solver_count=1, seeded=False, baseline_solver_id="baseline")),
    )
    monkeypatch.setattr(main_execution, "ExecutionEngine", MagicMock(return_value=execution_engine))
    monkeypatch.setattr(main_execution, "SignalEngine", MagicMock(return_value=signal_engine))
    monkeypatch.setattr("orion.execution.position_manager.PositionManager", MagicMock(return_value=position_manager))
    monkeypatch.setattr("orion.processing.rules.exit_rules.get_default_exit_rules", MagicMock(return_value=[]))
    monkeypatch.setattr(main_execution, "_publish_execution_liveness_until_cancelled", startup_liveness)
    monkeypatch.setattr(main_execution, "publish_liveness", AsyncMock())

    with pytest.raises(ServiceLeaseLostError):
        await asyncio.wait_for(main_execution.run_execution_service(shutdown), timeout=2.0)

    assert shutdown.is_set()
