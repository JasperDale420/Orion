"""`persist_exit_decision` records the exit signal as it actually fired.

`exit_decisions.rule_id` / `urgency` are the audit trail for WHICH exit rule
closed a position. `candidate_id` is deliberately NOT written at close
submission: `PositionManager.initialize()` outer-joins `ExitDecision` on
`candidate_id` and treats any row as terminal, so linking at submission would
drop an accepted-but-unfilled, cancelled, or partially-filled close from the
rule-based recovery path after a restart. A row here means "a close was
submitted", not "the position is closed".
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
async def test_persist_exit_decision_records_rule_and_urgency_but_not_candidate_link() -> None:
    signal = SimpleNamespace(
        rule_id="stop_loss_v1",
        reason="stop loss hit: return=-41.0% <= -40.0%",
        urgency="IMMEDIATE",
        confidence=1.0,
        # Even a signal that carries the entry candidate must not link the row.
        candidate_id="cand-777",
        details={"bucket": "SWING", "pnl_pct": -41.0},
    )

    await persist_exit_decision("AAPL260918P00200000", signal, "orion_exit_1", {"id": "broker-1"})

    row = await _row("orion_exit_1")
    assert row.rule_id == "stop_loss_v1"
    assert row.urgency == "IMMEDIATE"
    assert row.broker_order_id == "broker-1"
    assert row.candidate_id is None, "an ExitDecision row is not closure; PositionManager treats it as terminal"


@pytest.mark.asyncio
async def test_exit_quote_is_stored_alongside_the_signal_details() -> None:
    """The decision-time market lands in `details.exit_quote` — the exit-side
    counterpart of the entry's `decision_trace_json.entry_quote`."""
    details = {"bucket": "SWING", "pnl_pct": -41.0}
    signal = SimpleNamespace(
        rule_id="stop_loss_v1", reason="stop loss hit", urgency="IMMEDIATE", confidence=1.0, details=details
    )
    quote = {"bid": 0.94, "ask": 1.06, "mid": 1.0, "spread_pct": 0.12, "source": "gateway_option_quote"}

    await persist_exit_decision("AAPL260918P00200000", signal, "orion_exit_2", {"id": "broker-2"}, exit_quote=quote)

    row = await _row("orion_exit_2")
    assert row.details["exit_quote"] == quote
    assert row.details["bucket"] == "SWING", "the rule's own details must survive"
    assert details == {"bucket": "SWING", "pnl_pct": -41.0}, "the caller's details dict must not be mutated"


@pytest.mark.asyncio
async def test_details_are_unchanged_when_no_exit_quote_is_available() -> None:
    signal = SimpleNamespace(
        rule_id="time_stop_v1", reason="time stop", urgency="IMMEDIATE", confidence=1.0, details={"bucket": "0DTE"}
    )

    await persist_exit_decision("SPY260918C00600000", signal, "orion_exit_3", {"id": "broker-3"})

    row = await _row("orion_exit_3")
    assert row.details == {"bucket": "0DTE"}
