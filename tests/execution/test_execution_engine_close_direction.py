from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from orion.execution.execution_engine import ExecutionEngine


def _make_engine() -> ExecutionEngine:
    """Build an engine with a REAL ``order_history`` and a real ``_record_result``.

    Leaving ``_record_result`` unmocked lets the close-path's broker round-trip
    outcome (success/failure) actually land in ``order_history`` so we can assert
    the circuit-breaker bookkeeping fired — not just that the broker call shape
    was right. Without this, a regression that submits the order but skips
    ``_record_result(True)`` (so the breaker never sees the success and a later
    failure trips it early) would pass a call-only assertion.
    """
    engine = ExecutionEngine.__new__(ExecutionEngine)
    engine._gateway_available = True
    engine._gateway_check_ts = datetime.now(UTC)
    engine.risk_manager = Mock()
    engine.order_history = []
    engine.last_positions_snapshot_ts = None
    engine._ledger = None
    engine._check_gateway_available = AsyncMock(return_value=True)
    return engine


@pytest.mark.asyncio
async def test_close_position_uses_buy_side_for_short_direction(monkeypatch: pytest.MonkeyPatch) -> None:
    import orion.execution.execution_engine as ee_mod

    monkeypatch.setattr(ee_mod, "persist_exit_decision", AsyncMock())

    engine = _make_engine()

    mock_client = AsyncMock()
    mock_client.close_position.return_value = {"id": "order-1", "status": "accepted"}
    engine._gateway_client = mock_client
    engine._get_gateway_client = lambda: mock_client

    exit_signal = SimpleNamespace(urgency="IMMEDIATE", reason="stop", rule_id="rule.stop", confidence=0.9, details={})

    mock_client.get_position = AsyncMock(return_value={"qty": "1"})
    closed = await engine.close_position(ticker="AAPL", qty=1.0, exit_signal=exit_signal, direction="SHORT")

    assert closed is True
    mock_client.close_position.assert_called_once_with("AAPL", qty=1.0)
    # State effect: the success round-trip was recorded for the circuit breaker.
    # A regression that returns True but never calls _record_result(True) would
    # leave the breaker blind to this success and trip early on the next failure.
    assert len(engine.order_history) == 1
    _ts, success = engine.order_history[-1]
    assert success is True
    # The decision was persisted to the exit-decision journal exactly once.
    ee_mod.persist_exit_decision.assert_awaited_once()
    # And no fallback to the limit (create_order) path on the IMMEDIATE branch.
    mock_client.create_order.assert_not_called()


@pytest.mark.asyncio
async def test_close_position_limit_order_for_long_direction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-immediate exit for LONG position uses limit sell order via Gateway."""
    import orion.execution.execution_engine as ee_mod

    monkeypatch.setattr(ee_mod, "persist_exit_decision", AsyncMock())

    engine = _make_engine()

    mock_client = AsyncMock()
    mock_client.get_stock_snapshot.return_value = {
        "latestTrade": {"p": 100.0},
    }
    mock_client.create_order.return_value = {"id": "order-2", "status": "accepted"}
    engine._gateway_client = mock_client
    engine._get_gateway_client = lambda: mock_client

    exit_signal = SimpleNamespace(urgency="NORMAL", reason="take_profit", rule_id="rule.tp", confidence=0.8, details={})

    mock_client.get_position = AsyncMock(return_value={"qty": "5"})
    closed = await engine.close_position(ticker="AAPL", qty=5.0, exit_signal=exit_signal, direction="LONG")

    assert closed is True
    mock_client.create_order.assert_called_once()
    call_kwargs = mock_client.create_order.call_args[1]
    assert call_kwargs["side"] == "sell"  # Closing LONG = sell
    assert call_kwargs["order_type"] == "limit"
    # State effect: success recorded, decision journaled, no IMMEDIATE/native fallback.
    assert engine.order_history[-1][1] is True
    ee_mod.persist_exit_decision.assert_awaited_once()
    mock_client.close_position.assert_not_called()
