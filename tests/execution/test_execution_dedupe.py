from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.execution.execution_engine import ExecutionEngine


@pytest.fixture
def engine():
    """Create ExecutionEngine with MCP mock for fill polling."""
    ee = ExecutionEngine()
    ee._gateway_available = True
    ee._gateway_check_ts = datetime.now(UTC)

    mock_client = AsyncMock()
    mock_client.get_positions.return_value = [
        {"symbol": "AAPL", "qty": "10", "avg_entry_price": "100.0", "market_value": "1050.0"}
    ]
    mock_client.get_account.return_value = {"equity": "50000.0", "last_equity": "50000.0"}

    ee._gateway_client = mock_client
    ee._get_gateway_client = lambda: mock_client
    yield ee


@pytest.mark.asyncio
async def test_poll_fills_updates_risk_state(engine):
    """Verify poll_fills refreshes risk state from MCP positions/account."""
    engine.risk_manager = MagicMock()
    # `_equity_seeded` defaults to False on a real RiskManager and gates
    # the one-shot seed of current_equity from the Gateway. On a MagicMock
    # `getattr(..., "_equity_seeded", False)` returns a truthy auto-attr
    # so we have to set it explicitly here.
    engine.risk_manager._equity_seeded = False

    await engine.poll_fills()

    # Verify account equity was seeded
    assert engine.risk_manager.current_equity == 50000.0
    assert engine.risk_manager._equity_seeded is True
    # Verify last fill poll timestamp was set
    assert engine._last_fill_poll_ts is not None
