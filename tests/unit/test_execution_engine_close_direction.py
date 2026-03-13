from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from alpaca.trading.enums import OrderSide
from orion.execution.execution_engine import ExecutionEngine


@pytest.mark.asyncio
async def test_close_position_uses_buy_side_for_short_direction() -> None:
    engine = ExecutionEngine.__new__(ExecutionEngine)
    engine.connector = Mock()
    engine.market_connector = Mock()
    engine.risk_manager = Mock()
    engine.order_history = []
    engine.last_positions_snapshot_ts = None
    engine.market_connector.get_latest_price.return_value = 100.0
    engine.connector.submit_market_order.return_value = SimpleNamespace(id="order-1")
    engine._persist_exit_decision = AsyncMock()  # type: ignore[method-assign]
    engine._record_result = Mock()  # type: ignore[method-assign]

    exit_signal = SimpleNamespace(urgency="IMMEDIATE", reason="stop", rule_id="rule.stop")

    closed = await engine.close_position(ticker="AAPL", qty=1.0, exit_signal=exit_signal, direction="SHORT")

    assert closed is True
    assert engine.connector.submit_market_order.called
    kwargs = engine.connector.submit_market_order.call_args.kwargs
    assert kwargs["side"] == OrderSide.BUY
