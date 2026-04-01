import pytest

from orion.config import RiskSettings
from orion.execution.risk.manager import RiskManager


@pytest.mark.asyncio
async def test_risk_manager_respects_solver_override():
    """
    Verifies that check_order prioritizes values in risk_override if provided.
    """
    # 1. Setup Global Config (Loose)
    global_cfg = RiskSettings(
        max_positions=10, risk_per_trade_pct=0.05, max_daily_loss=10000.0, max_ticker_exposure_usd=100000.0
    )
    rm = RiskManager(config=global_cfg)

    # Fake state: 1 position open
    rm.open_positions = 1
    rm.positions = {"AAPL": {"qty": 10, "avg_entry": 100}}

    # 2. Define Solver Override (Strict - Max 1 position)
    # If using this solver, we should REJECT opening a 2nd position,
    # even though global allows 10.
    solver_cfg = RiskSettings(
        max_positions=1,  # Strict
        risk_per_trade_pct=0.05,
    )

    # 3. Test Check Order with Override
    # Attempt to buy MSFT.
    # Current Open = 1. New = 2.
    # Global limit (10) -> Should Pass.
    # Solver limit (1) -> Should Fail.

    try:
        # If argument doesn't exist yet, this will raise TypeError (RED State)
        allowed = rm.check_order(ticker="MSFT", quantity=10, price=200, side="BUY", risk_override=solver_cfg)
    except TypeError:
        # This confirms we need to add the argument
        pytest.fail("check_order does not accept 'risk_override' argument yet.")

    assert allowed is False, "Risk Manager should have rejected order based on Solver Config override (Max Pos=1)"

    # 4. Verify Global Fallback
    # Without override, it should allow (1 < 10)
    allowed_global = rm.check_order(ticker="MSFT", quantity=10, price=200, side="BUY")
    assert allowed_global is True, "Risk Manager should fallback to global usage logic if no override"
