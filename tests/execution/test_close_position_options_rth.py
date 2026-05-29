"""Tests for the options-aware ExecutionEngine.close_position refactor.

Phase 2 of the exit-pipeline RCA (Bug #1).
docs/superpowers/plans/2026-05-20-exit-pipeline-fixes.md

Before the refactor, every fallback exit went through
``client.close_position(...)`` which submits a MARKET order.
Alpaca rejects options market orders outside 9:30am-4:00pm ET with
``42210000``. Even inside RTH, options market orders cross the
spread by an unbounded amount and may fill at very poor prices.

After the refactor:
  - Options outside RTH → log + return False without submitting.
  - Options inside RTH → marketable LIMIT order derived from the
    caller-supplied ``current_price`` (the mark the position monitor
    tracks via sync_positions), rounded to the options tick.
  - Equity behavior unchanged: market for IMMEDIATE, limit otherwise.
  - Options NEVER submit as market regardless of urgency.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from orion.execution.execution_engine import ExecutionEngine


def _make_engine(market_schedule_returns_options_open: bool = True) -> ExecutionEngine:
    engine = ExecutionEngine.__new__(ExecutionEngine)
    engine._gateway_available = True
    engine._gateway_check_ts = datetime.now(UTC)
    engine.risk_manager = Mock()
    engine.order_history = []
    engine.last_positions_snapshot_ts = None
    engine._ledger = None
    engine._check_gateway_available = AsyncMock(return_value=True)
    engine._record_result = Mock()
    # Patch market schedule via attribute injection — close_position will
    # use MarketSchedule().is_market_open_for_options(...) internally.
    # Easier to stub the schedule than to patch the singleton.
    engine._market_schedule = MagicMock()
    engine._market_schedule.is_market_open_for_options = Mock(return_value=market_schedule_returns_options_open)
    return engine


@pytest.mark.asyncio
async def test_options_close_outside_rth_skips_submit() -> None:
    """When options market is closed, close_position must NOT submit
    an order. It logs and returns False so the caller can retry next
    loop iteration. No call to gateway client.close_position or
    create_order should happen."""
    engine = _make_engine(market_schedule_returns_options_open=False)

    mock_client = AsyncMock()
    engine._gateway_client = mock_client
    engine._get_gateway_client = lambda: mock_client

    exit_signal = SimpleNamespace(
        urgency="IMMEDIATE",
        reason="time to expiry: 0.95 days <= min_dte=1",
        rule_id="time_to_expiry_v1",
        confidence=1.0,
        details={"bucket": "SWING"},
    )

    closed = await engine.close_position(
        ticker="NVDA260522C00250000",
        qty=10.0,
        exit_signal=exit_signal,
        direction="LONG",
        current_price=2.50,
    )

    assert closed is False
    # CRITICAL: must not have submitted any order
    mock_client.close_position.assert_not_called()
    mock_client.create_order.assert_not_called()


@pytest.mark.asyncio
async def test_options_close_inside_rth_uses_marketable_limit() -> None:
    """Inside RTH, options close uses a LIMIT order priced aggressively
    relative to current_price (the mark from sync_positions). For a
    LONG close (SELL), the limit should be below the mark to cross the
    spread; rounded to the options tick."""
    engine = _make_engine(market_schedule_returns_options_open=True)

    mock_client = AsyncMock()
    mock_client.create_order.return_value = {"id": "order-opt", "status": "accepted"}
    engine._gateway_client = mock_client
    engine._get_gateway_client = lambda: mock_client

    # patch persist_exit_decision since it does its own DB work
    import orion.execution.execution_engine as ee_mod

    original_persist = ee_mod.persist_exit_decision
    ee_mod.persist_exit_decision = AsyncMock()
    try:
        exit_signal = SimpleNamespace(
            urgency="IMMEDIATE",
            reason="profit target hit",
            rule_id="profit_target_v1",
            confidence=1.0,
            details={"bucket": "SWING"},
        )

        mock_client.get_position = AsyncMock(return_value={"qty": "10"})
        closed = await engine.close_position(
            ticker="NVDA260522C00250000",
            qty=10.0,
            exit_signal=exit_signal,
            direction="LONG",
            current_price=2.50,
        )
    finally:
        ee_mod.persist_exit_decision = original_persist

    assert closed is True
    # Must NOT use market path (no client.close_position call)
    mock_client.close_position.assert_not_called()
    # Must use limit
    mock_client.create_order.assert_called_once()
    call_kwargs = mock_client.create_order.call_args[1]
    assert call_kwargs["order_type"] == "limit"
    assert call_kwargs["side"] == "sell"  # LONG close → SELL
    # Limit should be aggressive (below mark by 5-10%) and on the
    # $0.05 tick (under $3 → 0.05 increments). Range: 2.50 * [0.90, 0.95]
    # = [2.25, 2.375]. Both must land on 0.05 increments.
    limit = call_kwargs["limit_price"]
    assert 2.20 <= limit <= 2.40, f"limit {limit} outside expected aggressive band for $2.50 mark"
    # Must be on $0.05 tick
    cents = round(limit * 100)
    assert cents % 5 == 0, f"limit {limit} not on $0.05 tick"


@pytest.mark.asyncio
async def test_options_close_inside_rth_short_close_buys_above_mark() -> None:
    """A SHORT options close (BUY to cover) must lift the offer.

    Broker reports SHORT positions with NEGATIVE qty — this test
    reflects that wire reality. The close path derives side from
    `qty < 0` (held_short), not from the caller's `direction` hint
    (which can be stale; see test_options_close_side_from_qty_sign
    below for the regression that motivated this change)."""
    engine = _make_engine(market_schedule_returns_options_open=True)

    mock_client = AsyncMock()
    mock_client.create_order.return_value = {"id": "order-opt", "status": "accepted"}
    engine._gateway_client = mock_client
    engine._get_gateway_client = lambda: mock_client

    import orion.execution.execution_engine as ee_mod

    original_persist = ee_mod.persist_exit_decision
    ee_mod.persist_exit_decision = AsyncMock()
    try:
        exit_signal = SimpleNamespace(
            urgency="IMMEDIATE",
            reason="time to expiry",
            rule_id="time_to_expiry_v1",
            confidence=1.0,
            details={"bucket": "SWING"},
        )

        mock_client.get_position = AsyncMock(return_value={"qty": "-5"})
        closed = await engine.close_position(
            ticker="SPY260522P00450000",
            qty=-5.0,  # negative = broker says SHORT
            exit_signal=exit_signal,
            direction="SHORT",
            current_price=4.00,
        )
    finally:
        ee_mod.persist_exit_decision = original_persist

    assert closed is True
    call_kwargs = mock_client.create_order.call_args[1]
    assert call_kwargs["order_type"] == "limit"
    assert call_kwargs["side"] == "buy"  # held_short → BUY-to-cover
    assert call_kwargs["qty"] == 5.0  # abs_qty
    # $4.00 is ≥ $3 → $0.10 tick. Lift offer by 5-10%: [4.20, 4.40].
    limit = call_kwargs["limit_price"]
    assert 4.10 <= limit <= 4.50, f"limit {limit} outside expected band"
    cents = round(limit * 100)
    assert cents % 10 == 0, f"limit {limit} not on $0.10 tick (>=$3 → 0.10 increments)"


@pytest.mark.asyncio
async def test_options_close_side_derived_from_qty_sign_not_direction() -> None:
    """Codex review 2026-05-21 Important #2: the options branch was
    deriving close_side from the caller's `direction` arg, but the
    broker's signed qty is the ground truth. Mismatch scenario:
    `_sync_risk_from_gateway` populates positions opened by sibling
    Empire systems on the shared Alpaca account with a default
    `direction="LONG"` even when the broker holds them SHORT
    (negative qty). The close would then BUY-to-cover MORE of the
    same position instead of SELLing to close.

    This test pins the contract: side MUST come from qty sign, not
    direction. Broker holds the position LONG (qty=+10) but caller
    says direction='SHORT' (stale) → side must be SELL (LONG close),
    not BUY (what the buggy code would do).
    """
    engine = _make_engine(market_schedule_returns_options_open=True)

    mock_client = AsyncMock()
    mock_client.create_order.return_value = {"id": "order-opt", "status": "accepted"}
    engine._gateway_client = mock_client
    engine._get_gateway_client = lambda: mock_client

    import orion.execution.execution_engine as ee_mod

    original_persist = ee_mod.persist_exit_decision
    ee_mod.persist_exit_decision = AsyncMock()
    try:
        exit_signal = SimpleNamespace(
            urgency="IMMEDIATE",
            reason="test",
            rule_id="r",
            confidence=1.0,
            details={"bucket": "SWING"},
        )
        mock_client.get_position = AsyncMock(return_value={"qty": "10"})  # broker LONG
        closed = await engine.close_position(
            ticker="NVDA260522C00250000",
            qty=10.0,  # positive — broker says LONG
            exit_signal=exit_signal,
            direction="SHORT",  # stale/mislabeled — must NOT win
            current_price=2.50,
        )
    finally:
        ee_mod.persist_exit_decision = original_persist

    assert closed is True
    call_kwargs = mock_client.create_order.call_args[1]
    assert call_kwargs["side"] == "sell", (
        "broker qty sign (positive = LONG) MUST override caller direction='SHORT'; "
        "BUY would compound exposure instead of closing"
    )


@pytest.mark.asyncio
async def test_options_close_without_current_price_returns_false() -> None:
    """If the caller forgets to pass current_price for an options
    close, the engine has no mark to derive a limit from. It must
    return False with a log rather than fall back to market (which
    would be rejected by Alpaca anyway). Defensive guard."""
    engine = _make_engine(market_schedule_returns_options_open=True)

    mock_client = AsyncMock()
    engine._gateway_client = mock_client
    engine._get_gateway_client = lambda: mock_client

    exit_signal = SimpleNamespace(urgency="IMMEDIATE", reason="x", rule_id="r", confidence=1.0, details={})

    closed = await engine.close_position(
        ticker="NVDA260522C00250000",
        qty=10.0,
        exit_signal=exit_signal,
        direction="LONG",
        current_price=None,  # missing
    )

    assert closed is False
    mock_client.close_position.assert_not_called()
    mock_client.create_order.assert_not_called()


@pytest.mark.asyncio
async def test_equity_close_immediate_still_uses_market() -> None:
    """Equity behavior unchanged: IMMEDIATE urgency goes via the
    market-order close_position path. Only options are forced to
    limit. Equity has no RTH gate (Alpaca allows equity market
    orders during extended hours too)."""
    engine = _make_engine(market_schedule_returns_options_open=False)  # off-hours

    mock_client = AsyncMock()
    mock_client.close_position.return_value = {"id": "order-eq", "status": "accepted"}
    engine._gateway_client = mock_client
    engine._get_gateway_client = lambda: mock_client

    import orion.execution.execution_engine as ee_mod

    original_persist = ee_mod.persist_exit_decision
    ee_mod.persist_exit_decision = AsyncMock()
    try:
        exit_signal = SimpleNamespace(urgency="IMMEDIATE", reason="x", rule_id="r", confidence=1.0, details={})

        mock_client.get_position = AsyncMock(return_value={"qty": "10"})
        closed = await engine.close_position(
            ticker="AAPL",  # equity, not OCC
            qty=10.0,
            exit_signal=exit_signal,
            direction="LONG",
            current_price=180.0,
        )
    finally:
        ee_mod.persist_exit_decision = original_persist

    assert closed is True
    mock_client.close_position.assert_called_once_with("AAPL", qty=10.0)
    mock_client.create_order.assert_not_called()
