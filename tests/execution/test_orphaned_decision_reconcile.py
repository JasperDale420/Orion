"""Startup reconciliation of orphaned decisions (finalize/decision gap).

``_submit_options_order`` finalizes the OrderRecord (broker_order_id + status)
at ~execution_engine.py:1020, then the in-memory ``decision.executed_successfully``
is flipped and main_execution.py persists it via ``update_decision_status``.

If the process crashes between ``persist_order_finalize`` and that status
write, the OrderRecord is in a broker-terminal state (accepted / REJECTED)
while its linked StrategyDecision is stuck at ``PENDING`` — an order is live
at the broker but the decision row looks unprocessed. ``reconcile_orphaned_decisions``
repairs those decisions at startup by mirroring the order's terminal status.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from orion.execution.decision_persistence import reconcile_orphaned_decisions
from orion.storage.db import async_session_factory
from orion.storage.models_execution import OrderRecord
from orion.storage.models_gold import StrategyDecision


async def _add(*rows) -> None:
    async with async_session_factory() as session:
        for r in rows:
            session.add(r)
        await session.commit()


async def _decision_status(decision_id: str) -> str | None:
    async with async_session_factory() as session:
        stmt = select(StrategyDecision).where(StrategyDecision.decision_id == decision_id)
        rec = (await session.execute(stmt)).scalars().first()
        return rec.executed_successfully if rec else None


def _decision(decision_id: str, candidate_id: str, status: str) -> StrategyDecision:
    return StrategyDecision(
        decision_id=decision_id,
        candidate_id=candidate_id,
        ticker="AAPL",
        strategy_version_id="test",
        decision="EXECUTE",
        executed_successfully=status,
        timestamp_utc=datetime.now(UTC),
    )


def _order(decision_id: str, client_order_id: str, status: str, broker_order_id: str | None) -> OrderRecord:
    return OrderRecord(
        id=client_order_id,
        decision_id=decision_id,
        candidate_id="c",
        client_order_id=client_order_id,
        broker_order_id=broker_order_id,
        status=status,
        ticker="AAPL",
        side="buy",
        qty=1,
    )


@pytest.mark.asyncio
async def test_orphaned_pending_decision_with_submitted_order_repaired_to_true() -> None:
    await _add(
        _decision("dec-1", "cand-1", "PENDING"),
        _order("dec-1", "orion_1", "accepted", "broker-1"),
    )

    repaired = await reconcile_orphaned_decisions()

    assert repaired == 1
    assert await _decision_status("dec-1") == "TRUE"


@pytest.mark.asyncio
async def test_orphaned_pending_decision_with_rejected_order_repaired_to_false() -> None:
    await _add(
        _decision("dec-2", "cand-2", "PENDING"),
        _order("dec-2", "orion_2", "REJECTED", None),
    )

    repaired = await reconcile_orphaned_decisions()

    assert repaired == 1
    assert await _decision_status("dec-2") == "FALSE"


@pytest.mark.asyncio
async def test_pending_submit_order_is_not_reconciled() -> None:
    """A still-in-flight order (PENDING_SUBMIT) must NOT flip its decision.

    These are genuinely mid-submission, not orphaned — the running engine
    owns them.
    """
    await _add(
        _decision("dec-3", "cand-3", "PENDING"),
        _order("dec-3", "orion_3", "PENDING_SUBMIT", None),
    )

    repaired = await reconcile_orphaned_decisions()

    assert repaired == 0
    assert await _decision_status("dec-3") == "PENDING"


@pytest.mark.asyncio
async def test_already_finalized_decision_is_left_alone() -> None:
    """A decision already at a terminal status must not be touched."""
    await _add(
        _decision("dec-4", "cand-4", "TRUE"),
        _order("dec-4", "orion_4", "accepted", "broker-4"),
    )

    repaired = await reconcile_orphaned_decisions()

    assert repaired == 0
    assert await _decision_status("dec-4") == "TRUE"
