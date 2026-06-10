"""Fault-injection regression for the pre-``try`` risk-state leak.

``_submit_options_order`` reserves risk state in three steps that run BEFORE
the ``try:`` guarding the Gateway round-trip:

1. ``risk_manager.update_post_trade(...)`` — reserves a pending order
2. ``risk_manager.set_intended_position_greeks(...)`` — stashes projected greeks
3. ``persist_pending_order(...)`` — writes the PENDING_SUBMIT tracking row

The ``except Exception`` handler around the Gateway call compensates for
failures raised *inside* the try (remove pending order + clear intended
greeks + finalize the row to REJECTED). But if ``persist_pending_order``
itself raises (a DB error), the exception escapes the method: the greeks
stash and the pending-order reservation LEAK, inflating portfolio-greeks
checks for that ticker until the stale-pending prune.

This test injects a raising ``persist_pending_order`` and asserts the
reservation is rolled back and the decision is marked failed (not an
unhandled crash that takes down the execution loop).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

import orion.execution.execution_engine as exec_mod
from orion.core.enums import DecisionStatus
from orion.storage.models_gold import CandidateTrade, StrategyDecision


def _make_candidate_and_decision() -> tuple[CandidateTrade, StrategyDecision]:
    now = datetime.now(UTC)
    candidate = CandidateTrade(
        candidate_id="test_submit_fault",
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
        candidate_id="test_submit_fault",
        execution_params={},
    )
    return candidate, decision


def _make_engine() -> tuple[exec_mod.ExecutionEngine, MagicMock, MagicMock]:
    engine = exec_mod.ExecutionEngine()
    engine._check_system_health = AsyncMock(return_value=True)
    engine._gateway_available = True
    engine._gateway_check_ts = datetime.now(UTC)

    mock_client = AsyncMock()
    mock_client.get_clock.return_value = {"is_open": True}
    mock_client.get_option_chain.return_value = {
        "contracts": [{"contract_symbol": "AAPL260522C00150000", "bid": 1.90, "ask": 2.10}]
    }
    mock_client.create_order.return_value = {"id": "broker-abc", "status": "accepted"}
    engine._gateway_client = mock_client
    engine._get_gateway_client = lambda: mock_client

    risk = MagicMock()
    risk.config.enable_shorting = False
    risk.ticker_exposures = {}
    risk.current_equity = 100_000.0
    risk.check_order.return_value = True
    risk.update_post_trade = AsyncMock()
    risk.remove_pending_order = AsyncMock()
    engine.risk_manager = risk

    return engine, mock_client, risk


@pytest.mark.asyncio
async def test_persist_pending_order_failure_rolls_back_reservation(monkeypatch) -> None:
    """If ``persist_pending_order`` raises, the engine must compensate, not leak.

    Pre-fix: the exception escapes ``_submit_options_order`` (no try around the
    persist), leaving the pending-order reservation and intended greeks stashed
    and crashing the caller.

    Post-fix: the engine removes the pending order, clears intended greeks,
    marks the decision failed, swallows the error, and returns.
    """
    engine, mock_client, risk = _make_engine()
    candidate, decision = _make_candidate_and_decision()

    # Inject a DB fault on the pre-try persist step.
    async def boom(**_kwargs):
        raise RuntimeError("simulated DB error on persist_pending_order")

    monkeypatch.setattr(exec_mod, "persist_pending_order", boom)

    # Must NOT propagate — an unhandled raise here would crash the execution loop.
    await engine.execute_order(decision, candidate)

    # (c) decision marked failed (mirrors the existing except block).
    assert decision.executed_successfully == DecisionStatus.FALSE

    # Gateway must never have been reached (persist precedes the try).
    mock_client.create_order.assert_not_called()

    # (a) the pending-order reservation was removed for the client_order_id.
    client_order_id = decision.execution_params.get("client_order_id")
    assert client_order_id is not None
    risk.remove_pending_order.assert_awaited_once_with(client_order_id)

    # (b) intended greeks were cleared for the ticker (no stale stash).
    risk.clear_intended_position_greeks.assert_called_once_with("AAPL")


@pytest.mark.asyncio
async def test_persist_pending_order_reraises_on_db_failure(monkeypatch) -> None:
    """The real ``persist_pending_order`` must RE-RAISE on a DB failure.

    This is the load-bearing half of the two-phase guarantee (RCA 2026-05-21):
    if the durable PENDING_SUBMIT row can't be written, the caller must NOT
    proceed to the broker. A previous version swallowed the exception and
    returned normally, which both defeated the durability guarantee AND made
    the engine's compensation path dead code. This asserts the contract the
    compensation test above relies on actually holds in production.
    """
    import orion.execution.persistence as persistence_mod

    class _BoomFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            raise RuntimeError("simulated DB outage opening session")

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(persistence_mod, "async_session_factory", _BoomFactory())

    candidate, decision = _make_candidate_and_decision()
    # @db_retry (tenacity) exhausts its 3 attempts then raises RetryError
    # wrapping the RuntimeError. The contract that matters: it RAISES (a
    # subclass of Exception, which the engine's compensation block catches) —
    # it must NOT return normally and let the broker call proceed.
    with pytest.raises(Exception) as excinfo:  # noqa: B017,PT011
        await persistence_mod.persist_pending_order(
            decision=decision,
            candidate=candidate,
            client_order_id="orion_test_reraise",
            side="BUY",
            qty=1,
            limit_price=2.0,
        )
    assert excinfo.value is not None
