from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orion.config import system_settings
from orion.execution.execution_engine import ExecutionEngine
from orion.storage.models_gold import CandidateTrade, StrategyDecision


@pytest.fixture
def engine():
    # Patch credentials and Connectors to avoid real init
    with (
        patch.object(system_settings, "alpaca_api_key", "test_key"),
        patch.object(system_settings, "alpaca_secret_key", "test_secret"),
        patch("orion.execution.execution_engine.AlpacaTradingConnector"),
        patch("orion.execution.execution_engine.AlpacaMarketConnector"),
        patch("orion.execution.execution_engine.AlpacaOptionsConnector"),
    ):
        return ExecutionEngine()


@pytest.mark.asyncio
async def test_execution_fresh_signal(engine):
    """
    Test that execution proceeds when signal is fresh.
    """
    # Mock System Health Check to pass
    engine._check_system_health = AsyncMock(return_value=True)
    engine.connector = MagicMock()
    engine.risk_manager = MagicMock()
    # Mock risk checks passing
    engine.risk_manager.config.enable_shorting = True
    engine.risk_manager.check_order.return_value = True
    engine.risk_manager.calculate_size.return_value = 10
    engine.risk_manager.update_post_trade = AsyncMock()  # Fix await usage

    # Mock market connector
    engine.market_connector = MagicMock()
    engine.market_connector.get_latest_price.return_value = 100.0

    # Create Candidate (Fresh)
    now = datetime.now(timezone.utc)
    cand = CandidateTrade(
        ticker="AAPL", timestamp_utc=now, direction="LONG", rule_id="test_rule", evidence={"signal_id": "sig1"}
    )
    decision = StrategyDecision(decision="EXECUTE")

    await engine.execute_order(decision, cand)

    assert decision.executed_successfully == "TRUE"
    assert engine.connector.submit_limit_order.called


@pytest.mark.asyncio
async def test_execution_stale_signal(engine):
    """
    Test that execution is blocked when signal is stale.
    """
    engine._check_system_health = AsyncMock(return_value=True)
    engine.connector = MagicMock()

    # Create Candidate (Stale by 5 mins)
    now = datetime.now(timezone.utc)
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
    assert not engine.connector.submit_limit_order.called
