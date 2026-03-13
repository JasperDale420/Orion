import asyncio
from datetime import datetime

import pytest
from sqlalchemy import insert

from orion.storage.models import SystemStatus
from orion.storage.models_gold import CandidateTrade, StrategyDecision


@pytest.mark.asyncio
async def test_circuit_breaker_blocks_trade():
    """
    Verifies that ExecutionEngine aborts trade when SystemStatus is UNHEALTHY.
    """
    # 1. Setup Environment
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    # In-memory DB
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

    # Monkeypatch
    import orion.storage.db

    orion.storage.db.engine = test_engine
    orion.storage.db.async_session_factory = test_session_factory

    from orion.storage.db import Base

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Reload Execution Engine to pick up patches
    import importlib

    import orion.execution.execution_engine

    importlib.reload(orion.execution.execution_engine)
    from orion.execution.execution_engine import ExecutionEngine

    # 2. Seed Unhealthy Status
    async with test_session_factory() as session:
        await session.execute(
            insert(SystemStatus).values(key="global_health", status="UNHEALTHY", details="Lag > 60s Test")
        )
        await session.commit()

    from unittest.mock import patch

    # 3. Attempt Execution
    with (
        patch("orion.execution.execution_engine.AlpacaTradingConnector"),
        patch("orion.execution.execution_engine.AlpacaOptionsConnector"),
        patch("orion.execution.execution_engine.AlpacaMarketConnector"),
    ):
        engine = ExecutionEngine()
        # Mock Connector to bypass "No connector" check
        engine.connector = True
        engine.market_connector = True

        # Create Candidate (Needed for signature)
        candidate = CandidateTrade(
            candidate_id="c1",
            ticker="SPY",
            timestamp_utc=datetime.utcnow(),
            rule_id="r1",
            direction="LONG",
            evidence={},
        )

    # Create Decision
    decision = StrategyDecision(
        decision_id="test_cb_1",
        candidate_id="c1",
        timestamp_utc=datetime.utcnow(),
        ticker="SPY",
        strategy_version_id="v1",
        decision="EXECUTE",
        execution_params={"order_type": "MARKET"},
        executed_successfully=None,
    )

    await engine.execute_order(decision, candidate)

    # 4. Assert Blocked
    assert decision.executed_successfully == "FALSE"
    assert decision.executed_successfully == "FALSE"
    # Note: 'execution_log' might not be available on model if not added in schema.
    # We rely on 'executed_successfully' being set to "FALSE" by circuit breaker.

    print(f"\nSUCCESS: Trade blocked. Status: {decision.executed_successfully}")


if __name__ == "__main__":
    asyncio.run(test_circuit_breaker_blocks_trade())
