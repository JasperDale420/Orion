from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.config import system_settings
from orion.execution.execution_engine import ExecutionEngine
from orion.storage.models_gold import CandidateTrade, StrategyDecision


def _make_mcp_client_mock():
    """Create a mock MCP client that returns successful responses."""
    mock = AsyncMock()
    mock.get_market_clock.return_value = {"is_open": True}
    mock.get_stock_snapshot.return_value = {
        "latestTrade": {"p": 100.0},
        "latestQuote": {"bp": 99.95, "ap": 100.05},
    }
    mock._call_tool.return_value = {"id": "order-123", "status": "accepted"}
    return mock


@pytest.fixture
def engine():
    ee = ExecutionEngine()
    # Set up MCP mock so execute_order proceeds
    ee._mcp_available = True
    ee._mcp_check_ts = datetime.now(UTC)
    mock_client = _make_mcp_client_mock()
    ee._mcp_client = mock_client
    ee._get_mcp_client = lambda: mock_client
    return ee


@pytest.mark.asyncio
async def test_execution_fresh_signal(engine):
    """
    Test that execution proceeds when signal is fresh.
    """
    # Mock System Health Check to pass
    engine._check_system_health = AsyncMock(return_value=True)
    engine.risk_manager = MagicMock()
    # Mock risk checks passing
    engine.risk_manager.config.enable_shorting = True
    engine.risk_manager.check_order.return_value = True
    engine.risk_manager.check_sector_exposure.return_value = True
    engine.risk_manager.calculate_size.return_value = 10
    engine.risk_manager.update_post_trade = AsyncMock()
    engine.risk_manager.remove_pending_order = AsyncMock()

    # Create Candidate (Fresh)
    now = datetime.now(UTC)
    cand = CandidateTrade(
        ticker="AAPL", timestamp_utc=now, direction="LONG", rule_id="test_rule", evidence={"signal_id": "sig1"}
    )
    decision = StrategyDecision(decision="EXECUTE")

    await engine.execute_order(decision, cand)

    assert decision.executed_successfully == "TRUE"
    # Verify order was placed via MCP
    mock_client = engine._mcp_client
    mock_client._call_tool.assert_called_once()


@pytest.mark.asyncio
async def test_execution_stale_signal(engine):
    """
    Test that execution is blocked when signal is stale.
    """
    engine._check_system_health = AsyncMock(return_value=True)

    # Create Candidate (Stale by 5 mins)
    now = datetime.now(UTC)
    stale_ts = now - timedelta(minutes=5)
    cand = CandidateTrade(
        ticker="AAPL", timestamp_utc=stale_ts, direction="LONG", rule_id="test_rule", evidence={"signal_id": "sig1"}
    )
    decision = StrategyDecision(decision="EXECUTE")

    # Set lag limit to 60s
    with patch.object(system_settings, "max_data_lag_seconds", 60):
        await engine.execute_order(decision, cand)

    assert decision.executed_successfully == "FALSE"
    assert "Data Lag" in decision.reason
    # No order should have been placed
    mock_client = engine._mcp_client
    mock_client._call_tool.assert_not_called()
