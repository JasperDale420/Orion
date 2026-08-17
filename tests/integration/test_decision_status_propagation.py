"""The execution loop must persist the engine's own terminal status.

``ExecutionEngine.execute_order`` distinguishes a business-reason skip
(illiquid quote, earnings window, bucket/underlying cap) from a genuine
failure (risk rejection, price-fetch failure, missing expiry). Collapsing
both to ``FALSE`` at persistence makes "we chose not to trade" and "the
trade attempt failed" indistinguishable in ``strategy_decisions``.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from orion import main_execution
from orion.core.enums import DecisionAction, DecisionStatus
from orion.storage.db import async_session_factory
from orion.storage.models_gold import StrategyDecision


def _execute_decision(candidate_id: str) -> StrategyDecision:
    """An EXECUTE decision carrying the fields save_decision requires."""
    return StrategyDecision(
        decision_id=str(uuid.uuid4()),
        candidate_id=candidate_id,
        ticker="SPY",
        strategy_version_id="V1_TEST_SOLVER",
        decision=DecisionAction.EXECUTE.value,
        p_take=0.6,
        timestamp_utc=datetime.now(UTC),
        executed_successfully=DecisionStatus.PENDING,
        decision_trace_json={"expected_return_bp": 25.0, "risk_score": 0.2},
    )


async def _run_one_candidate(monkeypatch, on_execute) -> StrategyDecision | None:
    """Drive one candidate through the real loop and return the stored row."""
    shutdown = asyncio.Event()
    candidate = SimpleNamespace(
        ticker="SPY",
        candidate_id=f"cand_{uuid.uuid4()}",
        direction="LONG",
        rule_id="test_rule",
        confidence=0.9,
        option_symbol="SPY260418C00500000",
    )
    decision = _execute_decision(candidate.candidate_id)

    async def execute_order(dec, _candidate):
        try:
            on_execute(dec)
        finally:
            shutdown.set()

    execution_engine = MagicMock()
    execution_engine.acquire_service_lease = AsyncMock()
    execution_engine.initialize = AsyncMock()
    execution_engine.poll_fills = AsyncMock()
    execution_engine.execute_order = execute_order
    execution_engine.gateway_positions_snapshot = None
    execution_engine.risk_manager = MagicMock()

    signal_engine = MagicMock()
    signal_engine.initialize = AsyncMock()
    signal_engine.decide = AsyncMock(return_value=decision)

    position_manager = MagicMock()
    position_manager.initialize = AsyncMock()
    position_manager.get_open_positions.return_value = []

    async def startup_liveness(_shutdown_event: asyncio.Event) -> None:
        await asyncio.Event().wait()

    async def fetch_candidates():
        return [] if shutdown.is_set() else [candidate]

    async def preflight(*_args, **_kwargs):
        return SimpleNamespace(ok=True, reason=None, extra={})

    monkeypatch.setattr("orion.execution.signal_preflight.preflight_live_signal", preflight)
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
    monkeypatch.setattr(main_execution, "publish_liveness", AsyncMock())

    await asyncio.wait_for(main_execution.run_execution_service(shutdown), timeout=5.0)

    async with async_session_factory() as session:
        stmt = select(StrategyDecision).where(StrategyDecision.decision_id == decision.decision_id)
        return (await session.execute(stmt)).scalars().first()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_business_skip_persists_as_skipped(monkeypatch) -> None:
    """An illiquid-quote skip stores SKIPPED, not FALSE."""

    def skip(decision):
        decision.executed_successfully = DecisionStatus.SKIPPED
        decision.reason = "Illiquid: spread 40% > max 15%"

    stored = await _run_one_candidate(monkeypatch, skip)

    assert stored is not None
    assert stored.executed_successfully == DecisionStatus.SKIPPED
    assert stored.reason == "Illiquid: spread 40% > max 15%"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_genuine_failure_persists_as_false(monkeypatch) -> None:
    """A risk rejection still stores FALSE."""

    def fail(decision):
        decision.executed_successfully = DecisionStatus.FALSE
        decision.reason = "Risk Rejection"

    stored = await _run_one_candidate(monkeypatch, fail)

    assert stored is not None
    assert stored.executed_successfully == DecisionStatus.FALSE


@pytest.mark.asyncio
@pytest.mark.integration
async def test_submitted_order_persists_as_true(monkeypatch) -> None:
    def submitted(decision):
        decision.executed_successfully = DecisionStatus.TRUE

    stored = await _run_one_candidate(monkeypatch, submitted)

    assert stored is not None
    assert stored.executed_successfully == DecisionStatus.TRUE


@pytest.mark.asyncio
@pytest.mark.integration
async def test_engine_leaving_status_pending_persists_as_false(monkeypatch) -> None:
    """A decision the engine never resolved must not be stored as PENDING.

    ``reconcile_orphaned_decisions`` repairs PENDING rows from their linked
    order record; a decision that never reached the broker has no such record
    and would stay PENDING forever.
    """

    def leave_pending(decision):
        return None

    stored = await _run_one_candidate(monkeypatch, leave_pending)

    assert stored is not None
    assert stored.executed_successfully == DecisionStatus.FALSE


@pytest.mark.asyncio
@pytest.mark.integration
async def test_execution_exception_persists_as_false(monkeypatch) -> None:
    def boom(decision):
        decision.executed_successfully = DecisionStatus.SKIPPED
        raise RuntimeError("gateway exploded")

    stored = await _run_one_candidate(monkeypatch, boom)

    assert stored is not None
    assert stored.executed_successfully == DecisionStatus.FALSE
