from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.config import system_settings
from orion.execution.execution_engine import ExecutionEngine
from orion.storage.models_gold import CandidateTrade, StrategyDecision


def _make_gateway_client_mock():
    """Create a mock Gateway client that returns successful responses."""
    mock = AsyncMock()
    mock.get_clock.return_value = {"is_open": True}
    mock.get_option_chain.return_value = {"contracts": [{"symbol": "AAPL260418C00150000", "mid": 2.0, "ask": 2.10}]}
    mock.create_order.return_value = {"id": "order-123", "status": "accepted"}
    return mock


@pytest.fixture
def engine():
    ee = ExecutionEngine()
    ee._gateway_available = True
    ee._gateway_check_ts = datetime.now(UTC)
    mock_client = _make_gateway_client_mock()
    ee._gateway_client = mock_client
    ee._get_gateway_client = lambda: mock_client
    return ee


@pytest.mark.asyncio
async def test_execution_fresh_signal(engine):
    """
    Test that execution proceeds when signal is fresh.
    """
    engine._check_system_health = AsyncMock(return_value=True)
    engine.risk_manager = MagicMock()
    engine.risk_manager.config.enable_shorting = True
    engine.risk_manager.check_order.return_value = True
    engine.risk_manager.check_sector_exposure.return_value = True
    engine.risk_manager.current_equity = 100000.0
    engine.risk_manager.update_post_trade = AsyncMock()
    engine.risk_manager.remove_pending_order = AsyncMock()

    now = datetime.now(UTC)
    cand = CandidateTrade(
        ticker="AAPL",
        timestamp_utc=now,
        direction="LONG",
        rule_id="test_rule",
        evidence={"signal_id": "sig1"},
        option_symbol="AAPL260418C00150000",
        premium=2.0,
        expiration_date=now + timedelta(days=30),
    )
    decision = StrategyDecision(decision="EXECUTE")

    await engine.execute_order(decision, cand)

    assert decision.executed_successfully == "TRUE"
    mock_client = engine._gateway_client
    mock_client.create_order.assert_called_once()


@pytest.mark.asyncio
async def test_execution_stale_signal(engine):
    """
    Test that execution is blocked when signal is stale.
    """
    engine._check_system_health = AsyncMock(return_value=True)

    now = datetime.now(UTC)
    stale_ts = now - timedelta(minutes=5)
    cand = CandidateTrade(
        ticker="AAPL",
        timestamp_utc=stale_ts,
        direction="LONG",
        rule_id="test_rule",
        evidence={"signal_id": "sig1"},
        option_symbol="AAPL260418C00150000",
        premium=2.0,
        expiration_date=now + timedelta(days=30),
    )
    decision = StrategyDecision(decision="EXECUTE")

    with patch.object(system_settings, "max_data_lag_seconds", 60):
        await engine.execute_order(decision, cand)

    assert decision.executed_successfully == "FALSE"
    assert "Data Lag" in decision.reason
    mock_client = engine._gateway_client
    mock_client.create_order.assert_not_called()
