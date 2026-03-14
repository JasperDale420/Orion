import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.storage.models_gold import CandidateTrade, StrategyDecision


# Mock Env Vars to ensure ExecutionEngine initializes
@pytest.fixture
def mock_env():
    with patch.dict(
        os.environ, {"ALPACA_API_KEY": "test_key", "ALPACA_SECRET_KEY": "test_secret", "ALPACA_PAPER": "True"}
    ):
        yield


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


@pytest.mark.asyncio
async def test_execution_enforces_limit_order(mock_env):
    from orion.execution.execution_engine import ExecutionEngine

    engine = ExecutionEngine()
    engine._check_system_health = AsyncMock(return_value=True)
    engine._mcp_available = True
    engine._mcp_check_ts = datetime.now(UTC)

    # Set up mock MCP client
    mock_client = _make_mcp_client_mock()
    engine._mcp_client = mock_client
    engine._get_mcp_client = lambda: mock_client

    # Mock Risk Manager behavior
    engine.risk_manager = MagicMock()
    engine.risk_manager.config.enable_shorting = False
    engine.risk_manager.ticker_exposures = {}
    engine.risk_manager.calculate_size.return_value = 10
    engine.risk_manager.check_order.return_value = True
    engine.risk_manager.check_sector_exposure.return_value = True
    engine.risk_manager.update_post_trade = AsyncMock()
    engine.risk_manager.remove_pending_order = AsyncMock()

    # Create Candidate
    candidate = CandidateTrade(
        candidate_id="test_id",
        ticker="AAPL",
        timestamp_utc=datetime.now(UTC),
        rule_id="test_rule",
        direction="LONG",
        evidence={},
    )
    decision = StrategyDecision(
        decision="EXECUTE",
        timestamp_utc=datetime.now(UTC),
        strategy_version_id="test",
        ticker="test",
        candidate_id="test",
    )

    # Run
    await engine.execute_order(decision, candidate)

    # Assert order was placed via MCP with limit type
    mock_client._call_tool.assert_called_once()
    call_args = mock_client._call_tool.call_args
    assert call_args[0][0] == "place_order"
    order_args = call_args[0][1]
    assert order_args["symbol"] == "AAPL"
    assert order_args["side"] == "buy"
    assert order_args["type"] == "limit"
    # buffer 10bps -> 100 * 1.0010 = 100.10
    assert order_args["limit_price"] == 100.10
    assert order_args["qty"] == 10


@pytest.mark.asyncio
async def test_execution_blocks_shorting(mock_env):
    from orion.execution.execution_engine import ExecutionEngine

    engine = ExecutionEngine()
    engine._check_system_health = AsyncMock(return_value=True)
    engine._mcp_available = True
    engine._mcp_check_ts = datetime.now(UTC)

    mock_client = _make_mcp_client_mock()
    engine._mcp_client = mock_client
    engine._get_mcp_client = lambda: mock_client

    engine.risk_manager = MagicMock()
    # Strictly disable shorting
    engine.risk_manager.config.enable_shorting = False
    # We hold nothing, so selling is opening a short
    engine.risk_manager.ticker_exposures = {"AAPL": 0.0}

    candidate = CandidateTrade(
        candidate_id="test_id",
        ticker="AAPL",
        timestamp_utc=datetime.now(UTC),
        rule_id="test_rule",
        direction="SHORT",
        evidence={},
    )
    decision = StrategyDecision(
        decision="EXECUTE",
        timestamp_utc=datetime.now(UTC),
        strategy_version_id="test",
        ticker="test",
        candidate_id="test",
    )

    await engine.execute_order(decision, candidate)

    # Assert NO order submitted
    mock_client._call_tool.assert_not_called()


@pytest.mark.asyncio
async def test_execution_allows_closing_short_disabled(mock_env):
    """If we already hold a position (LONG), selling is just closing, not shorting."""
    from orion.execution.execution_engine import ExecutionEngine

    engine = ExecutionEngine()
    engine._check_system_health = AsyncMock(return_value=True)
    engine._mcp_available = True
    engine._mcp_check_ts = datetime.now(UTC)

    mock_client = _make_mcp_client_mock()
    engine._mcp_client = mock_client
    engine._get_mcp_client = lambda: mock_client

    engine.risk_manager = MagicMock()
    engine.risk_manager.config.enable_shorting = False
    # We hold AAPL worth $1000, so selling is closing
    engine.risk_manager.ticker_exposures = {"AAPL": 1000.0}
    engine.risk_manager.calculate_size.return_value = 5
    engine.risk_manager.check_order.return_value = True
    engine.risk_manager.check_sector_exposure.return_value = True
    engine.risk_manager.update_post_trade = AsyncMock()
    engine.risk_manager.remove_pending_order = AsyncMock()

    candidate = CandidateTrade(
        candidate_id="test_id",
        ticker="AAPL",
        timestamp_utc=datetime.now(UTC),
        rule_id="test_rule",
        direction="SHORT",  # "SHORT" direction might be used by signal to indicate "SELL"
        evidence={},
    )
    decision = StrategyDecision(
        decision="EXECUTE",
        timestamp_utc=datetime.now(UTC),
        strategy_version_id="test",
        ticker="test",
        candidate_id="test",
    )

    await engine.execute_order(decision, candidate)

    # Should proceed - order placed via MCP
    mock_client._call_tool.assert_called_once()
