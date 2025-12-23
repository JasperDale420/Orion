import pytest
from orion.execution.risk_manager import RiskManager


@pytest.mark.asyncio
async def test_process_fill_long_profit():
    rm = RiskManager()
    rm.current_equity = 10000.0
    rm.current_daily_loss = 0.0

    # 1. Buy 10 @ 100
    await rm.process_fill("AAPL", 10, 100.0, "buy")
    pos = rm.positions["AAPL"]
    assert pos["qty"] == 10
    assert pos["avg_entry"] == 100.0

    # 2. Sell 10 @ 110 (Profit 100)
    await rm.process_fill("AAPL", 10, 110.0, "sell")
    pos = rm.positions["AAPL"]
    assert pos["qty"] == 0

    assert rm.current_equity == 10100.0
    # Daily loss should act correctly (it was 0, profit 100 -> stays 0)
    assert rm.current_daily_loss == 0.0


@pytest.mark.asyncio
async def test_process_fill_long_loss():
    rm = RiskManager()
    rm.current_equity = 10000.0
    rm.current_daily_loss = 0.0

    # Buy 10 @ 100
    await rm.process_fill("AAPL", 10, 100.0, "buy")

    # Sell 10 @ 90 (Loss 100)
    await rm.process_fill("AAPL", 10, 90.0, "sell")

    assert rm.current_equity == 9900.0
    assert rm.current_daily_loss == 100.0


@pytest.mark.asyncio
async def test_process_fill_short_profit():
    rm = RiskManager()
    rm.current_equity = 10000.0

    # Short 10 @ 100 (Sell to open)
    # Using 'sell' side
    await rm.process_fill("AAPL", 10, 100.0, "sell")
    pos = rm.positions["AAPL"]
    assert pos["qty"] == -10
    assert pos["avg_entry"] == 100.0

    # Cover 10 @ 90 (Buy to close) -> Profit 100
    await rm.process_fill("AAPL", 10, 90.0, "buy")

    assert rm.current_equity == 10100.0
    assert rm.positions["AAPL"]["qty"] == 0


@pytest.mark.asyncio
async def test_process_fill_flip():
    rm = RiskManager()
    rm.current_equity = 10000.0

    # Long 10 @ 100
    await rm.process_fill("AAPL", 10, 100.0, "buy")

    # Sell 20 @ 110
    # Should close 10 @ 110 (Profit 100)
    # And open Short 10 @ 110
    await rm.process_fill("AAPL", 20, 110.0, "sell")

    pos = rm.positions["AAPL"]
    assert pos["qty"] == -10
    assert pos["avg_entry"] == 110.0  # New entry for the short
    assert rm.current_equity == 10100.0


@pytest.mark.asyncio
async def test_process_fill_averaging_up():
    rm = RiskManager()

    # Buy 10 @ 100
    await rm.process_fill("AAPL", 10, 100.0, "buy")

    # Buy 10 @ 110
    # Total Val = 1000 + 1100 = 2100. Total Qty = 20. Avg = 105.
    await rm.process_fill("AAPL", 10, 110.0, "buy")

    pos = rm.positions["AAPL"]
    assert pos["qty"] == 20
    assert pos["avg_entry"] == 105.0
