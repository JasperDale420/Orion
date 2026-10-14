"""Risk-boundary tests for the options ENTRY session gate.

A new option entry may reach the broker only when BOTH session authorities
positively confirm the regular options session is open:

- the exchange calendar (strict — a dead calendar never falls back to anything
  for an entry; the broker-clock fallback exists only so closes can shed risk),
- a fresh broker clock read (an error, a timeout, a rate-limited or otherwise
  non-2xx answer, or a malformed payload is "unknown", and unknown never admits
  new risk).

A blocked entry must leave nothing to unwind (no pending-order row, no risk
reservation) and must not be counted as a broker failure by the error-rate
circuit breaker. A confirmed-closed session is a skip; an unverifiable one is a
failure.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from orion.core.enums import DecisionStatus
from orion.storage.models_gold import CandidateTrade, StrategyDecision

pytestmark = pytest.mark.real_entry_session_gate

_OPTION_SYMBOL = "AAPL260418C00150000"


@pytest.fixture(autouse=True)
def _broker_env():
    with patch.dict(
        os.environ,
        {
            "ALPACA_API_KEY": "test_key",  # pragma: allowlist secret
            "ALPACA_SECRET_KEY": "test_secret",  # pragma: allowlist secret
            "ALPACA_PAPER": "True",
        },
    ):
        yield


def _schedule(*, open_: bool | None = None, raises: Exception | None = None) -> MagicMock:
    schedule = MagicMock()
    if raises is not None:
        schedule.is_market_open_for_options.side_effect = raises
    else:
        schedule.is_market_open_for_options.return_value = open_
    return schedule


def _make_engine(*, schedule: MagicMock, clock: Any = None, clock_raises: BaseException | None = None):
    from orion.execution.execution_engine import ExecutionEngine

    engine = ExecutionEngine()
    engine._ledger = None
    engine._check_system_health = AsyncMock(return_value=True)
    # The availability probe is cached as fresh, so the only clock read the
    # test controls is the one the entry gate makes.
    engine._gateway_available = True
    engine._gateway_check_ts = datetime.now(UTC)
    engine._market_schedule = schedule

    client = AsyncMock()
    if clock_raises is not None:
        client.get_clock.side_effect = clock_raises
    else:
        client.get_clock.return_value = clock
    client.get_option_chain.return_value = {
        "contracts": [{"contract_symbol": _OPTION_SYMBOL, "bid": 1.90, "ask": 2.10}]
    }
    client.create_order.return_value = {"id": "order-123", "status": "accepted"}
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    engine.risk_manager = MagicMock()
    engine.risk_manager.config.enable_shorting = False
    engine.risk_manager.ticker_exposures = {}
    engine.risk_manager.current_equity = 100_000.0
    engine.risk_manager.check_order.return_value = True
    engine.risk_manager.update_post_trade = AsyncMock()
    engine.risk_manager.remove_pending_order = AsyncMock()
    return engine, client


def _candidate_and_decision() -> tuple[CandidateTrade, StrategyDecision]:
    now = datetime.now(UTC)
    candidate = CandidateTrade(
        candidate_id="entry_gate",
        ticker="AAPL",
        timestamp_utc=now,
        rule_id="test_rule",
        direction="LONG",
        evidence={},
        option_symbol=_OPTION_SYMBOL,
        premium=2.0,
        expiration_date=now + timedelta(days=30),
    )
    decision = StrategyDecision(
        decision="EXECUTE",
        timestamp_utc=now,
        strategy_version_id="test",
        ticker="AAPL",
        candidate_id="entry_gate",
        execution_params={},
    )
    return candidate, decision


async def _run_entry(engine) -> tuple[StrategyDecision, AsyncMock]:
    candidate, decision = _candidate_and_decision()
    with patch("orion.execution.execution_engine.persist_pending_order", new=AsyncMock()) as persist:
        await engine.execute_order(decision, candidate)
    return decision, persist


def _assert_blocked_without_side_effects(engine, client: AsyncMock, persist: AsyncMock) -> None:
    client.create_order.assert_not_called()
    persist.assert_not_awaited()
    engine.risk_manager.update_post_trade.assert_not_awaited()
    # Not a broker round-trip: must not feed the error-rate circuit breaker.
    assert len(engine.order_history) == 0


@pytest.mark.asyncio
async def test_entry_blocked_when_calendar_is_dead_even_if_broker_says_open() -> None:
    """A dead calendar never admits new risk. The broker-clock fallback exists
    for closes only; an entry must not use it."""
    engine, client = _make_engine(
        schedule=_schedule(raises=RuntimeError("calendar unavailable")), clock={"is_open": True}
    )

    decision, persist = await _run_entry(engine)

    _assert_blocked_without_side_effects(engine, client, persist)
    assert decision.executed_successfully == DecisionStatus.FALSE
    assert "session" in (decision.reason or "").lower()


@pytest.mark.asyncio
async def test_entry_skipped_when_calendar_says_closed() -> None:
    engine, client = _make_engine(schedule=_schedule(open_=False), clock={"is_open": True})

    decision, persist = await _run_entry(engine)

    _assert_blocked_without_side_effects(engine, client, persist)
    assert decision.executed_successfully == DecisionStatus.SKIPPED


@pytest.mark.asyncio
async def test_entry_skipped_when_broker_clock_says_closed() -> None:
    """The broker is the authority that will accept or queue the order; an
    unscheduled closure the calendar does not know about must still block."""
    engine, client = _make_engine(schedule=_schedule(open_=True), clock={"is_open": False})

    decision, persist = await _run_entry(engine)

    _assert_blocked_without_side_effects(engine, client, persist)
    assert decision.executed_successfully == DecisionStatus.SKIPPED


@pytest.mark.parametrize(
    "clock",
    [
        {"is_open": True, "status_code": 429},  # rate-limited
        {"is_open": True, "status_code": 503},
        {"error": "Server error '500'"},
        {},  # missing is_open
        {"is_open": "true"},  # truthy, not the boolean
        ["not", "a", "dict"],  # malformed payload
        None,
    ],
)
@pytest.mark.asyncio
async def test_entry_blocked_when_broker_clock_is_unknown(clock: Any) -> None:
    engine, client = _make_engine(schedule=_schedule(open_=True), clock=clock)

    decision, persist = await _run_entry(engine)

    _assert_blocked_without_side_effects(engine, client, persist)
    assert decision.executed_successfully == DecisionStatus.FALSE


@pytest.mark.parametrize(
    "exc",
    [
        asyncio.TimeoutError(),
        httpx.ReadTimeout("timed out"),
        httpx.ConnectError("refused"),
        RuntimeError("gateway down"),
    ],
)
@pytest.mark.asyncio
async def test_entry_blocked_when_broker_clock_read_fails(exc: BaseException) -> None:
    engine, client = _make_engine(schedule=_schedule(open_=True), clock_raises=exc)

    decision, persist = await _run_entry(engine)

    _assert_blocked_without_side_effects(engine, client, persist)
    assert decision.executed_successfully == DecisionStatus.FALSE


@pytest.mark.asyncio
async def test_entry_submitted_when_both_authorities_confirm_open() -> None:
    engine, client = _make_engine(schedule=_schedule(open_=True), clock={"is_open": True})

    decision, persist = await _run_entry(engine)

    client.create_order.assert_called_once()
    persist.assert_awaited_once()
    assert decision.executed_successfully == DecisionStatus.TRUE


@pytest.mark.asyncio
async def test_entry_gate_rereads_the_clock_and_leaves_the_close_gate_caches_alone() -> None:
    """Each entry reads the broker clock fresh, and never writes the close
    path's cached verdicts — an entry's read must not delay or authorize an
    exit."""
    engine, client = _make_engine(schedule=_schedule(open_=True), clock={"is_open": False})

    await _run_entry(engine)
    await _run_entry(engine)

    assert client.get_clock.await_count == 2
    assert engine._clock_closed_until is None
    assert engine._clock_open_until is None


@pytest.mark.asyncio
async def test_entry_gate_does_not_read_the_clock_when_the_calendar_already_blocks() -> None:
    engine, client = _make_engine(schedule=_schedule(open_=False), clock={"is_open": True})

    await _run_entry(engine)

    client.get_clock.assert_not_awaited()


@pytest.mark.parametrize(
    ("second_clock", "expected_status"),
    [
        # FALSE even for a confirmed close: the attempt already wrote an order
        # row (finalized REJECTED), and startup reconciliation maps REJECTED to
        # FALSE — the decision must agree with its row.
        ({"is_open": False}, DecisionStatus.FALSE),  # session closed mid-flight
        ({"is_open": True, "status_code": 429}, DecisionStatus.FALSE),  # unverifiable at submit time
    ],
)
@pytest.mark.asyncio
async def test_entry_aborted_when_session_closes_between_gate_and_submit(
    second_clock: dict[str, Any], expected_status: DecisionStatus
) -> None:
    """The reservation and pending-row write between the first gate and the
    submit can cross the close. Alpaca queues a day order placed after the close
    rather than rejecting it, so the session is re-confirmed immediately before
    the submit; a failed re-check unwinds every reservation, finalizes the
    pending row as never submitted, and is not counted as a broker failure."""
    engine, client = _make_engine(schedule=_schedule(open_=True), clock={"is_open": True})
    client.get_clock.side_effect = [{"is_open": True}, second_clock]
    engine.risk_manager.clear_intended_position_greeks = MagicMock()
    candidate, decision = _candidate_and_decision()

    with (
        patch("orion.execution.execution_engine.persist_pending_order", new=AsyncMock()) as persist,
        patch("orion.execution.execution_engine.persist_order_finalize", new=AsyncMock()) as finalize,
    ):
        await engine.execute_order(decision, candidate)

    client.create_order.assert_not_called()
    persist.assert_awaited_once()
    engine.risk_manager.update_post_trade.assert_awaited_once()
    client_order_id = decision.execution_params["client_order_id"]
    engine.risk_manager.remove_pending_order.assert_awaited_once_with(client_order_id)
    engine.risk_manager.clear_intended_position_greeks.assert_called_once_with(candidate.ticker)
    finalize.assert_awaited_once()
    assert finalize.await_args.kwargs["client_order_id"] == client_order_id
    assert finalize.await_args.kwargs["broker_order"] is None
    assert finalize.await_args.kwargs["error_message"]
    assert len(engine.order_history) == 0
    assert decision.executed_successfully == expected_status
