import pytest

from orion.config import RiskSettings
from orion.execution.risk.manager import RiskManager


@pytest.mark.asyncio
async def test_risk_manager_fill_deduplication():
    """
    Verifies that RiskManager.process_fill is idempotent using the fill_id.
    """
    settings = RiskSettings(max_daily_loss=1000.0)
    rm = RiskManager(settings)

    # Initial State
    rm.current_equity = 10000.0
    rm.starting_equity = 10000.0
    assert rm.processed_fill_ids == set()

    ticker = "TEST"
    qty = 10
    price = 100.0
    side = "buy"
    fill_id = "fill_123"

    # 1. Process Fill First Time
    await rm.process_fill(ticker, qty, price, side, fill_id)

    assert fill_id in rm.processed_fill_ids
    assert rm.positions[ticker]["qty"] == 10
    assert rm.ticker_exposures[ticker] == pytest.approx(1000.0)

    # 2. Process Same Fill Again (Duplicate)
    # If it processed again, qty would become 20
    await rm.process_fill(ticker, qty, price, side, fill_id)

    assert rm.positions[ticker]["qty"] == 10  # Should remain 10
    assert rm.ticker_exposures[ticker] == pytest.approx(1000.0)

    # 3. Process New Fill
    fill_id_2 = "fill_456"
    await rm.process_fill(ticker, qty, price, side, fill_id_2)

    assert fill_id_2 in rm.processed_fill_ids
    assert rm.positions[ticker]["qty"] == 20
