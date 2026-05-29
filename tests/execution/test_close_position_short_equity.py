"""Regression tests for ExecutionEngine.close_position with SIGNED equity qty.

Bug: Alpaca returns SHORT equity positions with a negative ``qty`` (e.g.
CRNC qty=-8000.0).  PositionMonitor's ``GatewayPositionAdapter`` loads the
shared-account positions verbatim and ``execute_exits`` passes ``pos.qty``
straight into ``ExecutionEngine.close_position(qty=...)``.

Two things break in the close path when qty is signed:

1. Both ``client.close_position(ticker, qty=qty)`` (Gateway DELETE
   ``/positions/{symbol}?qty=-8000.0``) and ``client.create_order(...,
   qty=qty)`` (Gateway POST body) are rejected by Alpaca with
   ``invalid qty: -8000.0`` (HTTP 422), which surfaces in the logs as a
   stream of ``EXIT_ORDER_FAILED`` events on every position-monitor tick.

2. ``close_side`` is computed from the ``direction`` argument
   (``BUY if direction == 'SHORT' else SELL``).  For positions opened by
   sibling systems on the shared Alpaca account, the candidate-trades
   lookup in ``_fetch_entry_context`` misses and ``pos.direction`` falls
   back to the dataclass default ``'LONG'`` — producing SELL for what is
   in reality a SHORT-held equity position.  The qty sign is the broker's
   ground truth and must override the (possibly wrong) ``direction`` hint.

These tests pin down the fix: take ``abs(qty)`` and derive the close side
from the qty SIGN so the Gateway routes to BUY-to-cover for SHORT-held
and SELL-to-close for LONG-held equities.
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
    engine._market_schedule = MagicMock()
    engine._market_schedule.is_market_open_for_options = Mock(return_value=market_schedule_returns_options_open)
    return engine


@pytest.mark.asyncio
async def test_short_equity_immediate_close_sends_positive_qty() -> None:
    """SHORT equity close with IMMEDIATE urgency (market path).

    Position from broker: ``qty=-8000.0`` (signed, SHORT-held).
    Gateway ``close_position`` rejects negative ``qty`` query-string values;
    we must pass ``abs(qty)`` so Alpaca infers BUY-to-cover from the
    existing short side.
    """
    engine = _make_engine()

    mock_client = AsyncMock()
    mock_client.close_position.return_value = {"id": "order-eq", "status": "accepted"}
    engine._gateway_client = mock_client
    engine._get_gateway_client = lambda: mock_client

    import orion.execution.execution_engine as ee_mod

    original_persist = ee_mod.persist_exit_decision
    ee_mod.persist_exit_decision = AsyncMock()
    try:
        exit_signal = SimpleNamespace(
            urgency="IMMEDIATE",
            reason="ml exit",
            rule_id="ml_exit_SWING",
            confidence=0.9,
            details={},
        )

        mock_client.get_position = AsyncMock(return_value={"qty": "-8000"})
        closed = await engine.close_position(
            ticker="CRNC",
            qty=-8000.0,
            exit_signal=exit_signal,
            direction="SHORT",
            current_price=12.50,
        )
    finally:
        ee_mod.persist_exit_decision = original_persist

    assert closed is True
    # Gateway DELETE must receive a POSITIVE qty.
    mock_client.close_position.assert_called_once()
    _, call_kwargs = mock_client.close_position.call_args
    assert call_kwargs["qty"] == 8000.0, f"expected abs qty, got {call_kwargs['qty']}"
    # And no fallback to create_order.
    mock_client.create_order.assert_not_called()


@pytest.mark.asyncio
async def test_short_equity_limit_close_uses_buy_side_and_positive_qty() -> None:
    """SHORT equity close with NORMAL urgency (limit path).

    Must derive side=BUY (BUY-to-cover) from the qty SIGN — independent of
    the ``direction`` hint — and pass a POSITIVE qty to ``create_order``.
    """
    engine = _make_engine()

    mock_client = AsyncMock()
    mock_client.create_order.return_value = {"id": "order-eq-limit", "status": "accepted"}
    engine._gateway_client = mock_client
    engine._get_gateway_client = lambda: mock_client

    import orion.execution.execution_engine as ee_mod

    original_persist = ee_mod.persist_exit_decision
    ee_mod.persist_exit_decision = AsyncMock()
    try:
        exit_signal = SimpleNamespace(
            urgency="NORMAL",
            reason="take profit",
            rule_id="rule.tp",
            confidence=0.8,
            details={},
        )

        mock_client.get_position = AsyncMock(return_value={"qty": "-8000"})
        closed = await engine.close_position(
            ticker="CRNC",
            qty=-8000.0,
            exit_signal=exit_signal,
            # Sibling-system position; entry-context defaulted to LONG.
            # The qty sign must win over the wrong hint.
            direction="LONG",
            current_price=12.50,
        )
    finally:
        ee_mod.persist_exit_decision = original_persist

    assert closed is True
    mock_client.create_order.assert_called_once()
    call_kwargs = mock_client.create_order.call_args[1]
    assert call_kwargs["qty"] == 8000.0, f"expected abs qty, got {call_kwargs['qty']}"
    assert call_kwargs["side"] == "buy", f"expected BUY-to-cover, got {call_kwargs['side']}"
    assert call_kwargs["order_type"] == "limit"
    # Limit for a BUY-to-cover must be at-or-above the mark — selling below
    # the mark to a short-cover means we'd never fill. Within 1% of mark.
    limit = call_kwargs["limit_price"]
    assert limit >= 12.50, f"BUY-to-cover limit {limit} below mark 12.50 will not fill"
    assert limit <= 12.50 * 1.01, f"BUY-to-cover limit {limit} unreasonably above mark"


@pytest.mark.asyncio
async def test_long_equity_limit_close_unchanged_uses_sell() -> None:
    """Regression guard: LONG equity close (qty > 0) still uses SELL with
    the existing limit-below-mark behavior. Ensures the new sign-driven
    logic doesn't disturb the long path.
    """
    engine = _make_engine()

    mock_client = AsyncMock()
    mock_client.create_order.return_value = {"id": "order-eq-long", "status": "accepted"}
    engine._gateway_client = mock_client
    engine._get_gateway_client = lambda: mock_client

    import orion.execution.execution_engine as ee_mod

    original_persist = ee_mod.persist_exit_decision
    ee_mod.persist_exit_decision = AsyncMock()
    try:
        exit_signal = SimpleNamespace(
            urgency="NORMAL",
            reason="take profit",
            rule_id="rule.tp",
            confidence=0.8,
            details={},
        )

        mock_client.get_position = AsyncMock(return_value={"qty": "100"})
        closed = await engine.close_position(
            ticker="AAPL",
            qty=100.0,
            exit_signal=exit_signal,
            direction="LONG",
            current_price=200.0,
        )
    finally:
        ee_mod.persist_exit_decision = original_persist

    assert closed is True
    call_kwargs = mock_client.create_order.call_args[1]
    assert call_kwargs["qty"] == 100.0
    assert call_kwargs["side"] == "sell"
    # Long close limit goes BELOW the mark (sell, hit bid).
    assert call_kwargs["limit_price"] <= 200.0
