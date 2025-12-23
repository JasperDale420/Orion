import pytest
from orion.config import RiskSettings
from orion.execution.risk_manager import RiskManager


@pytest.mark.asyncio
async def test_risk_manager_race_condition():
    cfg = RiskSettings()
    cfg.max_ticker_exposure_usd = 2000.0
    cfg.max_order_size_usd = 5000.0

    rm = RiskManager(config=cfg)

    ticker = "RACE_TEST"

    # 1. Check Order 1 (Buy $1500) -> OK
    qty1 = 15
    price1 = 100.0
    assert rm.check_order(ticker, qty1, price1, "BUY") == True

    # 2. Simulate sending Order 1 (Add Pending)
    oid1 = "order_1"
    await rm.update_post_trade(ticker, qty1, price1, "BUY", order_id=oid1)

    # Verify pending state
    assert oid1 in rm.pending_orders
    assert rm.pending_orders[oid1] == (ticker, 1500.0)

    # 3. Check Order 2 (Buy $600) -> Should FAIL
    # Current Exposure (0) + Pending (1500) + New (600) = 2100 > 2000
    qty2 = 6
    price2 = 100.0
    assert rm.check_order(ticker, qty2, price2, "BUY") == False

    # 4. Simulate Fill of Order 1
    # - Process Fill (updates authoritative exposure)
    # - Remove Pending
    await rm.process_fill(ticker, qty1, price1, "BUY")
    rm.remove_pending_order(oid1)

    # Verify authoritative state
    assert getattr(rm, "ticker_exposures", {}).get(ticker) == 1500.0
    assert oid1 not in rm.pending_orders

    # 5. Check Order 2 again -> Should still FAIL
    # Current (1500) + Pending (0) + New (600) = 2100 > 2000
    assert rm.check_order(ticker, qty2, price2, "BUY") == False


@pytest.mark.asyncio
async def test_risk_manager_pending_cleared_on_fill():
    """Verify that we don't double count after fill + removal"""
    cfg = RiskSettings()
    cfg.max_ticker_exposure_usd = 2000.0

    rm = RiskManager(config=cfg)
    ticker = "DBL_COUNT"

    # Order 1: $1000
    qty = 10
    price = 100.0
    oid = "ord_dbl"

    await rm.update_post_trade(ticker, qty, price, "BUY", order_id=oid)

    # Fill happens
    await rm.process_fill(ticker, qty, price, "BUY")
    rm.remove_pending_order(oid)

    # Current Exposure should be 1000, not 2000
    current_exp = rm.ticker_exposures.get(ticker, 0.0)
    assert current_exp == 1000.0

    # Check Order 2 ($900) -> Should Pass (1000+900 < 2000)
    # If double closing, it would be 2000+900 > 2000 -> Fail
    assert rm.check_order(ticker, 9, 100.0, "BUY") == True
