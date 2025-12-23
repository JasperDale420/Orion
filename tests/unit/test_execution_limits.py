import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from alpaca.trading.enums import OrderSide
from orion.storage.models_gold import CandidateTrade, StrategyDecision


# Mock Env Vars to ensure ExecutionEngine initializes
@pytest.fixture
def mock_env():
    with patch.dict(
        os.environ, {"ALPACA_API_KEY": "test_key", "ALPACA_SECRET_KEY": "test_secret", "ALPACA_PAPER": "True"}
    ):
        yield


@pytest.mark.asyncio
async def test_execution_enforces_limit_order(mock_env):
    from orion.execution.execution_engine import ExecutionEngine

    # Setup
    # Patch TradingConnector in the module (top-level import)
    # Patch MarketConnector in its source (local import)
    with patch("orion.execution.execution_engine.AlpacaTradingConnector") as MockTrading, patch(
        "orion.execution.execution_engine.AlpacaMarketConnector"
    ) as MockMarket:
        engine = ExecutionEngine()
        engine._check_system_health = AsyncMock(return_value=True)

        # Mock Risk Manager behavior
        engine.risk_manager = MagicMock()
        engine.risk_manager.config.enable_shorting = False
        engine.risk_manager.ticker_exposures = {}
        engine.risk_manager.calculate_size.return_value = 10
        engine.risk_manager.check_order.return_value = True
        engine.risk_manager.update_post_trade = AsyncMock()
        engine.risk_manager.remove_pending_order = AsyncMock()

        # Mock Market Data (on the instance)
        engine.market_connector.get_latest_price.return_value = 100.0

        # Create Candidate
        candidate = CandidateTrade(
            candidate_id="test_id",
            ticker="AAPL",
            timestamp_utc=datetime.now(timezone.utc),
            rule_id="test_rule",
            direction="LONG",
            evidence={},
        )
        decision = StrategyDecision(
            decision="EXECUTE",
            timestamp_utc=datetime.now(timezone.utc),
            strategy_version_id="test",
            ticker="test",
            candidate_id="test",
        )

        # Run
        await engine.execute_order(decision, candidate)

        # Assert LIMIT order was used
        engine.connector.submit_limit_order.assert_called_once()
        engine.connector.submit_market_order.assert_not_called()

        call_args = engine.connector.submit_limit_order.call_args[1]
        assert call_args["symbol"] == "AAPL"
        assert call_args["side"] == OrderSide.BUY
        # buffer 10bps -> 100 * 1.0010 = 100.10
        assert call_args["limit_price"] == 100.10
        assert call_args["qty"] == 10


@pytest.mark.asyncio
async def test_execution_blocks_shorting(mock_env):
    from orion.execution.execution_engine import ExecutionEngine

    with patch("orion.execution.execution_engine.AlpacaTradingConnector"), patch(
        "orion.execution.execution_engine.AlpacaMarketConnector"
    ):
        engine = ExecutionEngine()
        engine._check_system_health = AsyncMock(return_value=True)
        engine.risk_manager = MagicMock()
        # Strictly disable shorting
        engine.risk_manager.config.enable_shorting = False
        # We hold nothing, so selling is opening a short
        engine.risk_manager.ticker_exposures = {"AAPL": 0.0}

        candidate = CandidateTrade(
            candidate_id="test_id",
            ticker="AAPL",
            timestamp_utc=datetime.now(timezone.utc),
            rule_id="test_rule",
            direction="SHORT",
            evidence={},
        )
        decision = StrategyDecision(
            decision="EXECUTE",
            timestamp_utc=datetime.now(timezone.utc),
            strategy_version_id="test",
            ticker="test",
            candidate_id="test",
        )

        await engine.execute_order(decision, candidate)

        # Assert NO order submitted
        engine.connector.submit_limit_order.assert_not_called()
        engine.connector.submit_market_order.assert_not_called()


@pytest.mark.asyncio
async def test_execution_allows_closing_short_disabled(mock_env):
    """If we already hold a position (LONG), selling is just closing, not shorting."""
    from orion.execution.execution_engine import ExecutionEngine

    with patch("orion.execution.execution_engine.AlpacaTradingConnector"), patch(
        "orion.execution.execution_engine.AlpacaMarketConnector"
    ):
        engine = ExecutionEngine()
        engine._check_system_health = AsyncMock(return_value=True)
        engine.risk_manager = MagicMock()
        engine.risk_manager.config.enable_shorting = False
        # We hold AAPL worth $1000, so selling is closing
        engine.risk_manager.ticker_exposures = {"AAPL": 1000.0}
        engine.risk_manager.calculate_size.return_value = 5
        engine.risk_manager.check_order.return_value = True
        engine.risk_manager.update_post_trade = AsyncMock()
        engine.risk_manager.remove_pending_order = AsyncMock()

        engine.market_connector.get_latest_price.return_value = 100.0

        candidate = CandidateTrade(
            candidate_id="test_id",
            ticker="AAPL",
            timestamp_utc=datetime.now(timezone.utc),
            rule_id="test_rule",
            direction="SHORT",  # "SHORT" direction might be used by signal to indicate "SELL"
            evidence={},
        )
        decision = StrategyDecision(
            decision="EXECUTE",
            timestamp_utc=datetime.now(timezone.utc),
            strategy_version_id="test",
            ticker="test",
            candidate_id="test",
        )

        await engine.execute_order(decision, candidate)

        # Should proceed
        engine.connector.submit_limit_order.assert_called_once()
