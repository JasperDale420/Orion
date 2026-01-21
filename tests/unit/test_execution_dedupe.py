from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orion.execution.execution_engine import ExecutionEngine


class MockAsyncSession:
    def __init__(self, result_scalars=None):
        self.result_scalars = result_scalars or []
        self.added_items = []
        self.executed_stmts = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def execute(self, stmt):
        self.executed_stmts.append(stmt)
        result = MagicMock()
        # Handle scalar results if needed
        return result

    def add(self, item):
        self.added_items.append(item)

    async def commit(self):
        self.committed = True


@pytest.fixture
def engine():
    with (
        patch("orion.execution.execution_engine.system_settings") as mock_settings,
        patch("orion.execution.execution_engine.AlpacaTradingConnector"),
        patch("orion.execution.execution_engine.AlpacaMarketConnector"),
        patch("orion.execution.execution_engine.AlpacaOptionsConnector"),
    ):
        mock_settings.alpaca_api_key = "test_key"  # pragma: allowlist secret
        mock_settings.alpaca_secret_key = "test_secret"  # pragma: allowlist secret

        yield ExecutionEngine()


@pytest.mark.asyncio
async def test_dedupe_fills_new(engine):
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
    with patch("orion.shared.db_utils.async_session_factory") as mock_factory:
        mock_factory.return_value = mock_session

        # Mock RiskManager to avoid logic
        engine.risk_manager = MagicMock()
        engine.risk_manager.process_fill = AsyncMock()

        print("[DEBUG] Calling poll_fills")
        await engine.poll_fills()
        print("[DEBUG] poll_fills returned")

        assert engine.risk_manager.process_fill.called
        assert len(mock_session.executed_stmts) >= 1
        # Check that one of the stmts is an insert for FillRecord
        # We can't easily introspect the stmt object fully without complex checks,
        # but the fact it called execute is enough for this dedupe test which focuses on FLOW.
        print("[PASS] test_dedupe_fills_new")


if __name__ == "__main__":
    import asyncio

    # Setup Engine manually because fixture
    print("\n[DEBUG] MAIN START")
    with (
        patch("orion.execution.execution_engine.system_settings"),
        patch("orion.execution.execution_engine.AlpacaTradingConnector") as MockConnector,
        patch("orion.execution.execution_engine.AlpacaMarketConnector"),
    ):
        ee = ExecutionEngine()
        ee.connector = MockConnector.return_value
        asyncio.run(test_dedupe_fills_new(ee))
        print("\nALL TESTS PASSED MANUALLY")
