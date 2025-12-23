from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orion.execution.execution_engine import ExecutionEngine


class MockAsyncSession:
    def __init__(self, result_scalars=None):
        self.result_scalars = result_scalars or []
        self.added_items = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def execute(self, stmt):
        result = MagicMock()
        result.scalars.return_value.first.return_value = self.result_scalars[0] if self.result_scalars else None
        return result

    def add(self, item):
        self.added_items.append(item)

    async def commit(self):
        self.committed = True


@pytest.fixture
def engine():
    print("\n[DEBUG] FIXTURE START")
    with patch("orion.execution.execution_engine.system_settings"), patch(
        "orion.execution.execution_engine.AlpacaTradingConnector"
    ), patch("orion.execution.execution_engine.AlpacaMarketConnector"):
        print("[DEBUG] Initializing ExecutionEngine")
        ee = ExecutionEngine()
        print("[DEBUG] ExecutionEngine Initialized")
        return ee


@pytest.mark.asyncio
async def test_dedupe_fills_new(engine):
    print("\n[DEBUG] TEST START: test_dedupe_fills_new")
    mock_fill = MagicMock()
    mock_fill.id = "order_123"
    mock_fill.client_order_id = "client_123"
    mock_fill.symbol = "AAPL"
    mock_fill.filled_qty = "10"
    mock_fill.filled_avg_price = "100.0"
    mock_fill.side = "buy"

    # Set mock return for connector (no to_thread patch)
    engine.connector.get_recent_fills.return_value = [mock_fill]

    # Mock DB session (Empty)
    mock_session = MockAsyncSession(result_scalars=None)

    # Patch session factory inside execution_engine
    with patch("orion.execution.execution_engine.async_session_factory") as mock_factory:
        mock_factory.return_value = mock_session

        # Mock RiskManager to avoid logic
        engine.risk_manager = MagicMock()
        engine.risk_manager.process_fill = AsyncMock()

        print("[DEBUG] Calling poll_fills")
        await engine.poll_fills()
        print("[DEBUG] poll_fills returned")

        assert engine.risk_manager.process_fill.called
        assert len(mock_session.added_items) == 1
        assert mock_session.added_items[0].fill_id == "order_123"
        print("[PASS] test_dedupe_fills_new")


if __name__ == "__main__":
    import asyncio

    # Setup Engine manually because fixture
    print("\n[DEBUG] MAIN START")
    with patch("orion.execution.execution_engine.system_settings"), patch(
        "orion.execution.execution_engine.AlpacaTradingConnector"
    ) as MockConnector, patch("orion.execution.execution_engine.AlpacaMarketConnector"):
        ee = ExecutionEngine()
        ee.connector = MockConnector.return_value
        asyncio.run(test_dedupe_fills_new(ee))
        print("\nALL TESTS PASSED MANUALLY")
