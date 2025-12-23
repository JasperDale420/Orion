from unittest.mock import MagicMock

import pytest
from alpaca.trading.enums import OrderSide, OrderType
from orion.execution.risk_manager import RiskManager


class MockOrder:
    def __init__(self, id, symbol, qty, side, type, limit_price, client_order_id):
        self.id = id
        self.symbol = symbol
        self.qty = qty
        self.side = side
        self.order_type = type
        self.limit_price = limit_price
        self.client_order_id = client_order_id


class MockAccount:
    def __init__(self, equity, last_equity):
        self.equity = equity
        self.last_equity = last_equity
        self.currency = "USD"
        self.buying_power = "200000"


@pytest.mark.asyncio
async def test_risk_manager_restart_pending_orders():
    """
    Verifies that RiskManager correctly hydrates pending orders from the broker's open orders on restart.
    """
    # 1. Setup RiskManager and Mock Connector
    risk_manager = RiskManager()
    connector = MagicMock()

    # Mock Account
    connector.client.get_account.return_value = MockAccount(100000.0, 99000.0)  # $1000 profit today

    # Mock Positions
    pos1 = MagicMock()
    pos1.symbol = "AAPL"
    pos1.market_value = "15000"
    pos1.qty = "100"
    pos1.avg_entry_price = "140"
    pos1.unrealized_pl = "1000"
    connector.client.get_all_positions.return_value = [pos1]

    # Mock Open Orders (The Critical Part)
    # Order 1: Buy 50 MSFT @ 300 (Cost $15,000)
    o1 = MockOrder(
        id="o1",
        symbol="MSFT",
        qty="50",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        limit_price="300.0",
        client_order_id="client_o1",
    )
    # Order 2: Sell 50 AAPL @ 160 (Cost -$8,000 impact, reduces exposure)
    o2 = MockOrder(
        id="o2",
        symbol="AAPL",
        qty="50",
        side=OrderSide.SELL,
        type=OrderType.LIMIT,
        limit_price="160.0",
        client_order_id="client_o2",
    )

    connector.client.get_orders.return_value = [o1, o2]

    # 2. Sync
    risk_manager.sync_with_broker(connector)

    # 3. Assertions

    # Check Pending Orders Map
    assert "client_o1" in risk_manager.pending_orders
    assert risk_manager.pending_orders["client_o1"] == ("MSFT", 15000.0)  # Buy is positive cost

    assert "client_o2" in risk_manager.pending_orders
    assert risk_manager.pending_orders["client_o2"] == ("AAPL", -8000.0)  # Sell is negative cost (signed)

    # 4. Check Risk Calculation Check
    # Current MSFT exposure should be 0 (no position), but projected should include pending.

    # Attempt to Buy MORE MSFT.
    # Max Ticker Exposure default is usually ~20k or 50k depending on config.
    # Let's assume standard config (e.g. 20% of 100k = 20k).

    # If we already have 15k pending MSFT, adding 10k more should fail if max is 20k.
    risk_manager.ticker_exposures["MSFT"] = 0.0  # From positions (none)

    # Simulate a check
    # Check Order: Buy 40 MSFT @ 300 = 12,000 USD
    # Existing Pending: 15,000 USD
    # Total Projected: 27,000 USD
    # If Limit is 25,000 USD (25% of 100k), this should FAIL.

    # Let's forcibly set a limit to verify logic
    risk_manager.config.max_ticker_exposure_usd = 20000.0
    risk_manager.config.max_order_size_usd = 50000.0  # Ensure order size doesn't trip

    allowed = risk_manager.check_order("MSFT", 40, 300.0, "buy")
    assert allowed is False, "Should be rejected due to pending order (15k) + new order (12k) = 27k > 20k Limit"

    # Check that without pending it would have passed (12k < 20k)
    # Setup a fresh RM with no pending
    rm_clean = RiskManager()
    rm_clean.config.max_ticker_exposure_usd = 20000.0
    rm_clean.config.max_order_size_usd = 50000.0
    rm_clean.ticker_exposures["MSFT"] = 0.0

    allowed_clean = rm_clean.check_order("MSFT", 40, 300.0, "buy")

    # Debug if fail
    if not allowed_clean:
        print(f"Clean Check Failed. Daily Loss: {rm_clean.current_daily_loss}, Equity: {rm_clean.current_equity}")

    assert allowed_clean is True, "Should be allowed without pending orders"


if __name__ == "__main__":
    import asyncio

    asyncio.run(test_risk_manager_restart_pending_orders())
