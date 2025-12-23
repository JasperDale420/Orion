import os

import pytest

# Force SQLite for unit tests
os.environ["DB_URL"] = "sqlite+aiosqlite:///:memory:"

from orion.config import RiskSettings
from orion.core.circuit_breaker import CircuitBreaker
from orion.execution.risk_manager import RiskManager
from orion.storage.db import async_session_factory, init_db
from orion.storage.models_risk import RiskState


@pytest.mark.asyncio
async def test_drawdown_kill_switch_opens_circuit_breaker():
    await init_db()

    cb = CircuitBreaker()
    await cb.open("reset")
    await cb.close()
    assert await cb.is_open() is False

    cfg = RiskSettings(max_daily_loss=1e9, max_drawdown_pct=0.05)
    rm = RiskManager(config=cfg)

    rm.current_equity = 1000.0
    rm.starting_equity = 1000.0
    rm.peak_equity = 1000.0
    rm.positions["SPY"] = {"qty": 10.0, "avg_entry": 100.0}

    # Sell 10 @ 90 => realized pnl = (90-100)*10 = -100 => 10% drawdown
    await rm.process_fill("SPY", qty=10.0, price=90.0, side="sell")

    assert await cb.is_open() is True

    async with async_session_factory() as session:
        state = await session.get(RiskState, "global_risk_v1")
        assert state is not None
        assert state.current_equity == pytest.approx(900.0)
        assert state.peak_equity == pytest.approx(1000.0)


@pytest.mark.asyncio
async def test_drawdown_kill_switch_trips_on_initialize_when_breached():
    await init_db()

    cb = CircuitBreaker()
    await cb.close()
    assert await cb.is_open() is False

    async with async_session_factory() as session:
        # Persist an already-breached state: 10% drawdown vs 5% limit.
        session.add(
            RiskState(
                id="global_risk_v1",
                current_daily_loss=0.0,
                current_equity=900.0,
                starting_equity=1000.0,
                peak_equity=1000.0,
                open_positions_count=1,
            )
        )
        await session.commit()

    cfg = RiskSettings(max_daily_loss=1e9, max_drawdown_pct=0.05)
    rm = RiskManager(config=cfg)
    await rm.initialize()

    assert await cb.is_open() is True
