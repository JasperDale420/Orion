import os

# Force SQLite for unit tests
os.environ["DB_URL"] = "sqlite+aiosqlite:///:memory:"


import pytest
from orion.execution.risk_manager import RiskManager
from orion.storage.db import async_session_factory, init_db
from sqlalchemy import text


@pytest.mark.asyncio
async def test_risk_persistence():
    # 1. Setup DB
    await init_db()

    # Clean table
    async with async_session_factory() as session:
        await session.execute(text("DELETE FROM risk_state"))
        await session.commit()

    # 2. R1: Initialize and update state
    rm1 = RiskManager()
    await rm1.initialize()

    # Verify defaults
    assert rm1.current_daily_loss == 0.0

    # Simulate a loss
    # Simulate a loss
    rm1.current_daily_loss = 500.0
    # Create 2 positions
    await rm1.process_fill("T1", 10, 100.0, "buy")
    await rm1.process_fill("T2", 10, 100.0, "buy")

    # 3. R2: New Instance, load state
    rm2 = RiskManager()
    await rm2.initialize()

    assert rm2.current_daily_loss == 500.0
    assert rm2.open_positions == 2

    # 4. Simulate Post Trade Update -> Use process_fill for persistence
    # Simulate Broker Sync finding the 2 positions
    rm2.positions["T1"] = {"qty": 10.0, "avg_entry": 100.0}
    rm2.positions["T2"] = {"qty": 10.0, "avg_entry": 100.0}

    # Buy 10 AAPL @ 150 (Cost 1500). Exposure +1500.
    await rm2.process_fill("AAPL", 10, 150.0, "buy")

    # Check R3
    rm3 = RiskManager()
    await rm3.initialize()

    # Should have updated open positions
    assert rm3.open_positions == 3
