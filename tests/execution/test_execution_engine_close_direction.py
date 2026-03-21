from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from orion.execution.execution_engine import ExecutionEngine


@pytest.mark.asyncio
async def test_close_position_uses_buy_side_for_short_direction() -> None:
    engine = ExecutionEngine.__new__(ExecutionEngine)
    engine._gateway_available = True
    engine._gateway_check_ts = datetime.now(UTC)
    engine.risk_manager = Mock()
    engine.order_history = []
    engine.last_positions_snapshot_ts = None
    engine._ledger = None

    mock_client = AsyncMock()
    mock_client.close_position.return_value = {"id": "order-1", "status": "accepted"}
    engine._gateway_client = mock_client
    engine._get_gateway_client = lambda: mock_client
    engine._check_gateway_available = AsyncMock(return_value=True)

    engine._persist_exit_decision = AsyncMock()
    engine._record_result = Mock()

    exit_signal = SimpleNamespace(urgency="IMMEDIATE", reason="stop", rule_id="rule.stop", confidence=0.9, details={})

    closed = await engine.close_position(ticker="AAPL", qty=1.0, exit_signal=exit_signal, direction="SHORT")

    assert closed is True
    mock_client.close_position.assert_called_once_with("AAPL", qty=1.0)


@pytest.mark.asyncio
async def test_close_position_limit_order_for_long_direction() -> None:
    """Non-immediate exit for LONG position uses limit sell order via Gateway."""
    engine = ExecutionEngine.__new__(ExecutionEngine)
    engine._gateway_available = True
    engine._gateway_check_ts = datetime.now(UTC)
    engine.risk_manager = Mock()
    engine.order_history = []
    engine.last_positions_snapshot_ts = None
    engine._ledger = None

    mock_client = AsyncMock()
    mock_client.get_stock_snapshot.return_value = {
        "latestTrade": {"p": 100.0},
    }
    mock_client.create_order.return_value = {"id": "order-2", "status": "accepted"}
    engine._gateway_client = mock_client
    engine._get_gateway_client = lambda: mock_client
    engine._check_gateway_available = AsyncMock(return_value=True)

    engine._persist_exit_decision = AsyncMock()
    engine._record_result = Mock()

    exit_signal = SimpleNamespace(urgency="NORMAL", reason="take_profit", rule_id="rule.tp", confidence=0.8, details={})

    closed = await engine.close_position(ticker="AAPL", qty=5.0, exit_signal=exit_signal, direction="LONG")

    assert closed is True
    mock_client.create_order.assert_called_once()
    call_kwargs = mock_client.create_order.call_args[1]
    assert call_kwargs["side"] == "sell"  # Closing LONG = sell
    assert call_kwargs["order_type"] == "limit"
