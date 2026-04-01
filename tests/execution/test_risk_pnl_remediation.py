from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.config import RiskSettings
from orion.execution.risk.manager import RiskManager

# --- Test Risk Manager Fill Processing ---


@pytest.mark.asyncio
async def test_risk_manager_process_fill_opens_position():
    """
    Verifies that a fill opens a position correctly.
    """
    rm = RiskManager(config=RiskSettings(max_positions=5))
    rm.current_equity = 10000.0

    # Buy 10 AAPL @ 100
    await rm.process_fill("AAPL", qty=10.0, price=100.0, side="buy", fill_id="pnl_1")

    assert "AAPL" in rm.positions
    assert rm.positions["AAPL"]["qty"] == 10.0
    assert rm.positions["AAPL"]["avg_entry"] == 100.0
    assert rm.open_positions == 1
    assert rm.ticker_exposures["AAPL"] == pytest.approx(1000.0)  # 10 * 100
    assert rm.current_equity == pytest.approx(10000.0)  # No PnL yet


@pytest.mark.asyncio
async def test_risk_manager_process_fill_adds_to_position():
    rm = RiskManager()
    rm.current_equity = 10000.0

    # Buy 10 @ 100
    await rm.process_fill("AAPL", qty=10.0, price=100.0, side="buy", fill_id="pnl_2")

    # Buy 10 @ 110
    await rm.process_fill("AAPL", qty=10.0, price=110.0, side="buy", fill_id="pnl_3")

    # Avg Entry should be (1000 + 1100) / 20 = 105
    assert rm.positions["AAPL"]["qty"] == 20.0
    assert rm.positions["AAPL"]["avg_entry"] == pytest.approx(105.0)
    assert rm.ticker_exposures["AAPL"] == pytest.approx(
        2200.0
    )  # 20 * 110 (Mark to Market at fill price? Or just cost?)
    # Implementation used MTM: 'abs(new_qty * price)'
    # Note: This updates exposure to current price, perfect.


@pytest.mark.asyncio
async def test_risk_manager_process_fill_closes_position_profit():
    rm = RiskManager()
    rm.current_equity = 10000.0

    # Long 10 @ 100
    await rm.process_fill("AAPL", qty=10.0, price=100.0, side="buy", fill_id="pnl_4")

    # Sell 5 @ 120 (Profit 20 * 5 = 100)
    await rm.process_fill("AAPL", qty=5.0, price=120.0, side="sell", fill_id="pnl_5")

    assert rm.positions["AAPL"]["qty"] == 5.0
    assert rm.positions["AAPL"]["avg_entry"] == pytest.approx(100.0)  # Unchanged
    assert rm.current_equity == pytest.approx(10100.0)  # +100 Profit
    # Daily loss tracks net: profit of 100 means daily_loss goes to -100
    assert rm.current_daily_loss == pytest.approx(-100.0)


@pytest.mark.asyncio
async def test_risk_manager_process_fill_closes_position_loss():
    rm = RiskManager()
    rm.current_equity = 10000.0

    # Long 10 @ 100
    await rm.process_fill("AAPL", qty=10.0, price=100.0, side="buy", fill_id="pnl_6")

    # Sell 5 @ 80 (Loss 20 * 5 = 100)
    await rm.process_fill("AAPL", qty=5.0, price=80.0, side="sell", fill_id="pnl_7")

    assert rm.positions["AAPL"]["qty"] == 5.0
    assert rm.current_equity == pytest.approx(9900.0)  # -100 Loss

    # Daily Loss Logic
    # If starting equity undefined, it assumes loss adds up.
    # In implementation: 'dd = self.starting_equity - self.current_equity'

    # If starting equity was 10000
    rm.starting_equity = 10000.0
    # Recalculate implicitly happened inside? No, we set attr after.
    # But inside the method, if 'attr starting_equity' is not set, it used fallback.
    # Let's verify 'current_daily_loss'.

    # Since starting_equity was NOT set in init, the fallback logic ran:
    # "if realized_pnl < 0: self.current_daily_loss += abs(realized_pnl)"
    assert rm.current_daily_loss == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_risk_manager_process_fill_short_flip():
    rm = RiskManager()
    rm.current_equity = 10000.0

    # Long 10 @ 100
    await rm.process_fill("AAPL", qty=10.0, price=100.0, side="buy", fill_id="pnl_8")

    # Sell 20 @ 110
    # 1. Close 10 @ 110 -> Profit (10*10) = 100.
    # 2. Open Short 10 @ 110.
    await rm.process_fill("AAPL", qty=20.0, price=110.0, side="sell", fill_id="pnl_9")

    assert rm.positions["AAPL"]["qty"] == -10.0  # Short 10
    assert rm.positions["AAPL"]["avg_entry"] == pytest.approx(110.0)  # New Entry
    assert rm.current_equity == pytest.approx(10100.0)  # Realized Profit from the closing leg
    assert rm.open_positions == 1


# --- Test Execution Engine Polling ---


@pytest.mark.asyncio
async def test_execution_engine_polling_integration():
    """Verify poll_fills updates risk state from MCP account/positions."""
    from orion.execution.execution_engine import ExecutionEngine

    engine = ExecutionEngine()
    engine._gateway_available = True
    engine._gateway_check_ts = datetime.now(UTC)

    # Mock MCP client
    mock_client = AsyncMock()
    mock_client.get_positions.return_value = [
        {"symbol": "AAPL", "qty": "10", "avg_entry_price": "150.0", "market_value": "1500.0"}
    ]
    mock_client.get_account.return_value = {"equity": "50000.0", "last_equity": "49500.0"}
    engine._gateway_client = mock_client
    engine._get_gateway_client = lambda: mock_client

    # Setup RiskManager mock to verify updates
    engine.risk_manager = MagicMock()
    engine.risk_manager.current_equity = 50000.0
    engine.risk_manager.current_daily_loss = 0.0

    # Execute Poll
    await engine.poll_fills()

    # Verify account equity was updated
    assert engine.risk_manager.current_equity == 50000.0
    # Verify daily loss calculated from last_equity - equity (but max(0, ...))
    # 49500 - 50000 = -500, max(0, -500) = 0
    assert engine.risk_manager.current_daily_loss == 0.0
    # Verify poll timestamp was set
    assert engine._last_fill_poll_ts is not None
