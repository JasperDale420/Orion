"""`persist_exit_decision` records the exit signal as it actually fired.

`exit_decisions.rule_id` / `urgency` are the audit trail for WHICH exit rule
closed a position, and `candidate_id` is what `bucket_metrics` and
`PositionManager` join back to the entry on. A signal that carries a candidate
id must land it in the row; older callers whose signals do not carry one
still persist (column stays NULL).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from orion.execution.persistence import persist_exit_decision
from orion.storage.db import async_session_factory
from orion.storage.models_gold import ExitDecision

pytestmark = pytest.mark.unit


async def _row(exit_id: str) -> ExitDecision:
    async with async_session_factory() as session:
        row = (await session.execute(select(ExitDecision).where(ExitDecision.exit_id == exit_id))).scalar_one()
        return row


@pytest.mark.asyncio
async def test_persist_exit_decision_records_rule_urgency_and_candidate() -> None:
    signal = SimpleNamespace(
        rule_id="stop_loss_v1",
        reason="stop loss hit: return=-41.0% <= -40.0%",
        urgency="SOON",
        confidence=1.0,
        candidate_id="cand-777",
        details={"bucket": "SWING", "pnl_pct": -41.0},
    )

    await persist_exit_decision("AAPL260918P00200000", signal, "orion_exit_1", {"id": "broker-1"})

    row = await _row("orion_exit_1")
    assert row.rule_id == "stop_loss_v1"
    assert row.urgency == "SOON"
    assert row.candidate_id == "cand-777"
    assert row.broker_order_id == "broker-1"


@pytest.mark.asyncio
async def test_persist_exit_decision_without_candidate_leaves_column_null() -> None:
    signal = SimpleNamespace(
        rule_id="ml_exit_SWING",
        reason="ML exit score: 0.80",
        urgency="IMMEDIATE",
        confidence=0.8,
        details={},
    )

    await persist_exit_decision("AAPL", signal, "orion_exit_2", None)

    row = await _row("orion_exit_2")
    assert row.rule_id == "ml_exit_SWING"
    assert row.candidate_id is None
