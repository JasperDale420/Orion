from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.execution.execution_engine import ExecutionEngine


@pytest.fixture
def engine():
    """Create ExecutionEngine with MCP mock for fill polling."""
    ee = ExecutionEngine()
    ee._mcp_available = True
    ee._mcp_check_ts = datetime.now(UTC)

    mock_client = AsyncMock()
    mock_client.get_positions.return_value = [
        {"symbol": "AAPL", "qty": "10", "avg_entry_price": "100.0", "market_value": "1050.0"}
    ]
    mock_client.get_account.return_value = {"equity": "50000.0", "last_equity": "50000.0"}

    ee._mcp_client = mock_client
    ee._get_mcp_client = lambda: mock_client
    yield ee


@pytest.mark.asyncio
async def test_poll_fills_updates_risk_state(engine):
    """Verify poll_fills refreshes risk state from MCP positions/account."""
    engine.risk_manager = MagicMock()

    await engine.poll_fills()

    # Verify account equity was updated
    assert engine.risk_manager.current_equity == 50000.0
    # Verify last fill poll timestamp was set
    assert engine._last_fill_poll_ts is not None
