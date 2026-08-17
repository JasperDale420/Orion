import pytest
from datetime import UTC, datetime


# A same-day broker timestamp: closing gains credit today's daily figure only
# when the fill can be placed in the current session.
_FILLED_AT = datetime.now(UTC)


@pytest.mark.asyncio
async def test_process_fill_long_profit(risk_manager_factory):
    rm = risk_manager_factory()
    rm.current_equity = 10000.0
    rm.current_daily_loss = 0.0

    # 1. Buy 10 @ 100
    await rm.process_fill("AAPL", 10, 100.0, "buy", fill_id="mock_1", filled_at=_FILLED_AT)
    pos = rm.positions["AAPL"]
    assert pos["qty"] == 10
    assert pos["avg_entry"] == pytest.approx(100.0)

    # 2. Sell 10 @ 110 (Profit 100)
    await rm.process_fill("AAPL", 10, 110.0, "sell", fill_id="mock_2", filled_at=_FILLED_AT)
    pos = rm.positions["AAPL"]
    assert pos["qty"] == 0

    assert rm.current_equity == pytest.approx(10100.0)
    # Daily loss tracks net: profit of 100 means daily_loss goes to -100
    assert rm.current_daily_loss == pytest.approx(-100.0)


@pytest.mark.asyncio
async def test_process_fill_long_loss(risk_manager_factory):
    rm = risk_manager_factory()
    rm.current_equity = 10000.0
    rm.current_daily_loss = 0.0

    # Buy 10 @ 100
    await rm.process_fill("AAPL", 10, 100.0, "buy", fill_id="mock_3", filled_at=_FILLED_AT)

    # Sell 10 @ 90 (Loss 100)
    await rm.process_fill("AAPL", 10, 90.0, "sell", fill_id="mock_4", filled_at=_FILLED_AT)

    assert rm.current_equity == pytest.approx(9900.0)
    assert rm.current_daily_loss == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_process_fill_short_profit(risk_manager_factory):
    rm = risk_manager_factory()
    rm.current_equity = 10000.0

    # Short 10 @ 100 (Sell to open)
    # Using 'sell' side
    await rm.process_fill("AAPL", 10, 100.0, "sell", fill_id="mock_5", filled_at=_FILLED_AT)
    pos = rm.positions["AAPL"]
    assert pos["qty"] == -10
    assert pos["avg_entry"] == pytest.approx(100.0)

    # Cover 10 @ 90 (Buy to close) -> Profit 100
    await rm.process_fill("AAPL", 10, 90.0, "buy", fill_id="mock_6", filled_at=_FILLED_AT)

    assert rm.current_equity == pytest.approx(10100.0)
    assert rm.positions["AAPL"]["qty"] == 0


@pytest.mark.asyncio
async def test_process_fill_flip(risk_manager_factory):
    rm = risk_manager_factory()
    rm.current_equity = 10000.0

    # Long 10 @ 100
    await rm.process_fill("AAPL", 10, 100.0, "buy", fill_id="mock_7", filled_at=_FILLED_AT)

    # Sell 20 @ 110
    # Should close 10 @ 110 (Profit 100)
    # And open Short 10 @ 110
    await rm.process_fill("AAPL", 20, 110.0, "sell", fill_id="mock_8", filled_at=_FILLED_AT)

    pos = rm.positions["AAPL"]
    assert pos["qty"] == -10
    assert pos["avg_entry"] == pytest.approx(110.0)  # New entry for the short
    assert rm.current_equity == pytest.approx(10100.0)


@pytest.mark.asyncio
async def test_process_fill_averaging_up(risk_manager_factory):
    rm = risk_manager_factory()

    # Buy 10 @ 100
    await rm.process_fill("AAPL", 10, 100.0, "buy", fill_id="mock_9", filled_at=_FILLED_AT)

    # Buy 10 @ 110
    # Total Val = 1000 + 1100 = 2100. Total Qty = 20. Avg = 105.
    await rm.process_fill("AAPL", 10, 110.0, "buy", fill_id="mock_10", filled_at=_FILLED_AT)

    pos = rm.positions["AAPL"]
    assert pos["qty"] == 20
    assert pos["avg_entry"] == pytest.approx(105.0)
