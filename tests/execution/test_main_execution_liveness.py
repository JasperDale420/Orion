import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from orion import main_execution
from orion.core.enums import DecisionAction


@pytest.mark.asyncio
async def test_startup_liveness_publishes_until_shutdown(monkeypatch) -> None:
    monkeypatch.setattr(main_execution, "_EXECUTION_STARTUP_LIVENESS_INTERVAL_SECONDS", 0.01)
    published = 0
    shutdown = asyncio.Event()

    async def publish(*_args, **_kwargs) -> None:
        nonlocal published
        published += 1
        if published >= 2:
            shutdown.set()

    monkeypatch.setattr(main_execution, "publish_liveness", publish)

    await asyncio.wait_for(main_execution._publish_execution_liveness_until_cancelled(shutdown), timeout=0.2)

    assert published >= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("gateway_snapshot", [None, {}])
async def test_execution_liveness_publishes_after_each_candidate(monkeypatch, gateway_snapshot) -> None:
    shutdown = asyncio.Event()
    candidates = [SimpleNamespace(ticker="AAPL"), SimpleNamespace(ticker="MSFT")]
    decision = SimpleNamespace(
        decision=DecisionAction.SKIP,
        decision_id="decision-1",
        executed_successfully=None,
        reason="skip",
        decision_trace_json=None,
    )

    async def startup_liveness(_shutdown_event: asyncio.Event) -> None:
        await asyncio.Event().wait()

    async def fetch_candidates():
        return candidates

    async def update_status(*_args, **_kwargs) -> None:
        if update_status.await_count >= 2:
            shutdown.set()

    update_status = AsyncMock(side_effect=update_status)

    execution_engine = MagicMock()
    execution_engine.acquire_service_lease = AsyncMock()
    execution_engine.initialize = AsyncMock()
    execution_engine.poll_fills = AsyncMock()
    execution_engine.gateway_positions_snapshot = gateway_snapshot

    signal_engine = MagicMock()
    signal_engine.initialize = AsyncMock()
    signal_engine.decide = AsyncMock(return_value=decision)

    position_manager = MagicMock()
    position_manager.initialize = AsyncMock()
    position_manager.reconcile_with_broker_positions = MagicMock()
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
    monkeypatch.setattr(main_execution, "auto_skip_stale_candidates", AsyncMock())
    monkeypatch.setattr(main_execution, "fetch_pending_candidates", fetch_candidates)
    monkeypatch.setattr(main_execution, "save_decision", AsyncMock())
    monkeypatch.setattr(main_execution, "update_decision_status", update_status)
    publish_liveness = AsyncMock()
    monkeypatch.setattr(main_execution, "publish_liveness", publish_liveness)

    await asyncio.wait_for(main_execution.run_execution_service(shutdown), timeout=1.0)

    assert update_status.await_count == 2
    assert publish_liveness.await_count >= 2
    if gateway_snapshot is None:
        position_manager.reconcile_with_broker_positions.assert_not_called()
        position_manager.get_open_positions.assert_not_called()
    else:
        position_manager.reconcile_with_broker_positions.assert_called_once_with({})
