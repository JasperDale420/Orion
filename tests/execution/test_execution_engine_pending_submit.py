"""Forensic regression test for the 2026-05-12..05-21 ghost-positions bug.

Between 2026-05-12 and 2026-05-21, Orion's signal pipeline produced 47
EXECUTE decisions that landed 37 options positions on the broker (visible
via Gateway /alpaca/positions) but ZERO matching rows in the `orders`
table. Root cause: ``_submit_options_order`` persisted the row AFTER
``client.create_order()`` returned. During the lease-conflict crash-loop
(380 container restarts in 24h), the process was killed in the window
between the broker accepting the order and the DB commit, so the order
landed on Alpaca with no DB tracking row — breaking attribution,
position sync, and exit logic.

Fix: write a PENDING_SUBMIT row BEFORE the Gateway round-trip, then
finalize it (SUBMITTED + broker_order_id on success; REJECTED +
error_message on failure) by client_order_id after the call returns.
Even if the process is yanked mid-Gateway-call, the tracking row
remains in PENDING_SUBMIT state and a startup reconciler can resolve
it against the broker by client_order_id.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from orion.storage.models_execution import OrderRecord
from orion.storage.models_gold import CandidateTrade, StrategyDecision


def _make_candidate_and_decision() -> tuple[CandidateTrade, StrategyDecision]:
    now = datetime.now(UTC)
    candidate = CandidateTrade(
        candidate_id="test_pending_submit",
        ticker="AAPL",
        timestamp_utc=now,
        rule_id="test_rule",
        direction="LONG",
        evidence={},
        option_symbol="AAPL260522C00150000",
        premium=2.0,
        expiration_date=now + timedelta(days=30),
    )
    decision = StrategyDecision(
        decision="EXECUTE",
        timestamp_utc=now,
        strategy_version_id="test",
        ticker="AAPL",
        candidate_id="test_pending_submit",
        execution_params={},
    )
    return candidate, decision


def _make_engine_with_real_persistence(*, create_order_side_effect=None, create_order_return=None):
    """Build an ExecutionEngine with a mocked Gateway client but REAL persistence.

    Persistence helpers use ``async_session_factory()`` directly, which the
    autouse conftest fixture has already bound to in-memory SQLite. So any
    row written by the engine lands in the test DB and is queryable.
    """
    from orion.execution.execution_engine import ExecutionEngine

    engine = ExecutionEngine()
    engine._check_system_health = AsyncMock(return_value=True)
    engine._gateway_available = True
    engine._gateway_check_ts = datetime.now(UTC)

    mock_client = AsyncMock()
    mock_client.get_clock.return_value = {"is_open": True}
    mock_client.get_option_chain.return_value = {
        "contracts": [{"contract_symbol": "AAPL260522C00150000", "bid": 1.90, "ask": 2.10}]
    }
    if create_order_side_effect is not None:
        mock_client.create_order.side_effect = create_order_side_effect
    elif create_order_return is not None:
        mock_client.create_order.return_value = create_order_return

    engine._gateway_client = mock_client
    engine._get_gateway_client = lambda: mock_client

    engine.risk_manager = MagicMock()
    engine.risk_manager.config.enable_shorting = False
    engine.risk_manager.ticker_exposures = {}
    engine.risk_manager.current_equity = 100_000.0
    engine.risk_manager.check_order.return_value = True
    engine.risk_manager.update_post_trade = AsyncMock()
    engine.risk_manager.remove_pending_order = AsyncMock()

    return engine, mock_client


async def _fetch_orion_orders() -> list[OrderRecord]:
    from orion.storage.db import async_session_factory

    async with async_session_factory() as session:
        stmt = select(OrderRecord).where(OrderRecord.client_order_id.like("orion_%"))
        result = await session.execute(stmt)
        return list(result.scalars().all())


@pytest.mark.asyncio
async def test_pending_submit_row_lands_before_gateway_call() -> None:
    """The PENDING_SUBMIT tracking row MUST exist in the DB before Gateway is called.

    This is the forensic regression: if the row only lands after the Gateway
    returns, a mid-call crash leaves the broker holding an untracked order.
    We verify ordering by snapshotting the DB from the mocked create_order
    callback — at the moment Gateway is "called", the row must already exist.
    """
    seen_rows_at_submit: list[OrderRecord] = []

    async def capture_db_state_at_gateway_call(**_kwargs):
        # Snapshot inside the mocked Gateway call — anything written before
        # this point is visible; anything written after is not.
        seen_rows_at_submit.extend(await _fetch_orion_orders())
        return {"id": "broker-abc", "status": "accepted"}

    engine, _ = _make_engine_with_real_persistence(create_order_side_effect=capture_db_state_at_gateway_call)
    candidate, decision = _make_candidate_and_decision()

    await engine.execute_order(decision, candidate)

    assert len(seen_rows_at_submit) == 1, (
        "A tracking row must be persisted BEFORE the Gateway round-trip. "
        f"At the moment client.create_order was invoked, found "
        f"{len(seen_rows_at_submit)} orion-prefixed rows; expected 1."
    )
    row = seen_rows_at_submit[0]
    assert row.status == "PENDING_SUBMIT", (
        f"Pre-submit row must carry status='PENDING_SUBMIT'; got {row.status!r}. "
        "A startup reconciler queries this status to find orphaned submissions."
    )
    assert row.broker_order_id is None, "Pre-submit row must not have broker_order_id yet (Gateway hasn't returned)."
    assert row.client_order_id is not None and row.client_order_id.startswith("orion_")
    assert row.ticker == "AAPL"
    assert row.qty > 0
    assert row.limit_price is not None and row.limit_price > 0


@pytest.mark.asyncio
async def test_row_remains_pending_submit_when_gateway_call_is_cancelled() -> None:
    """Simulates the 2026-05-21 crash-loop window: SIGTERM mid-Gateway-call.

    Models the failure as ``asyncio.CancelledError`` (BaseException in 3.8+,
    bypasses the ``except Exception`` handler that would otherwise finalize
    the row to REJECTED). This mirrors what happens when Docker sends
    SIGTERM during a Gateway round-trip — the task is cancelled before any
    cleanup code runs.

    Post-fix expectation: the PENDING_SUBMIT row remains in the DB,
    discoverable by a reconciler.
    """

    async def cancel_mid_call(**_kwargs):
        raise asyncio.CancelledError("simulated container SIGTERM mid-Gateway-call")

    engine, _ = _make_engine_with_real_persistence(create_order_side_effect=cancel_mid_call)
    candidate, decision = _make_candidate_and_decision()

    with pytest.raises(asyncio.CancelledError):
        await engine.execute_order(decision, candidate)

    rows = await _fetch_orion_orders()
    assert len(rows) == 1, (
        f"After a mid-Gateway cancel, expected exactly 1 orion row "
        f"(PENDING_SUBMIT tracking row); found {len(rows)}. "
        "Pre-fix: 0 rows would land (the row was written after the cancelled call)."
    )
    row = rows[0]
    assert row.status == "PENDING_SUBMIT", (
        f"After mid-Gateway cancel, row must remain in PENDING_SUBMIT state for the "
        f"startup reconciler; got status={row.status!r}."
    )
    assert row.broker_order_id is None
    assert row.client_order_id.startswith("orion_")


@pytest.mark.asyncio
async def test_row_finalizes_to_submitted_on_gateway_success() -> None:
    """Happy path: PENDING_SUBMIT row is updated to the broker status + id."""
    engine, _ = _make_engine_with_real_persistence(
        create_order_return={"id": "broker-xyz-success", "status": "accepted"}
    )
    candidate, decision = _make_candidate_and_decision()

    await engine.execute_order(decision, candidate)

    rows = await _fetch_orion_orders()
    assert len(rows) == 1, (
        f"After successful submit, exactly one row should exist (no duplicate insert); found {len(rows)}."
    )
    row = rows[0]
    assert row.broker_order_id == "broker-xyz-success"
    assert row.status == "accepted"
    assert row.error_message is None


@pytest.mark.asyncio
async def test_row_finalizes_to_rejected_on_gateway_error() -> None:
    """Gateway returns an error → row is updated to REJECTED with the error message.

    Models the 422 Unprocessable Entity case observed today — the row must
    be a single updated row, not a duplicate insert.
    """

    async def gateway_rejects(**_kwargs):
        raise RuntimeError("Gateway options order failed: 422 Unprocessable Entity")

    engine, _ = _make_engine_with_real_persistence(create_order_side_effect=gateway_rejects)
    candidate, decision = _make_candidate_and_decision()

    await engine.execute_order(decision, candidate)

    rows = await _fetch_orion_orders()
    assert len(rows) == 1, (
        f"Failed submit must update the PENDING_SUBMIT row in place, not create a "
        f"second row. Found {len(rows)} orion rows."
    )
    row = rows[0]
    assert row.status == "REJECTED"
    assert row.broker_order_id is None
    assert "422" in (row.error_message or "")
