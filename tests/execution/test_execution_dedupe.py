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
    # Use a real RiskManager so seed_equity_baseline actually executes
    # (a MagicMock would no-op the helper call). $50K is below the $100K
    # allocated-equity cap, so it seeds through unclamped.
    from orion.execution.risk.manager import RiskManager

    engine.risk_manager = RiskManager()

    await engine.poll_fills()

    # Verify account equity was seeded
    assert engine.risk_manager.current_equity == 50000.0
    assert engine.risk_manager._equity_seeded is True
    # Verify last fill poll timestamp was set
    assert engine._last_fill_poll_ts is not None
