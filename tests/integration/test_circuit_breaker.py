import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

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

    # 3. Attempt Execution with MCP mocked as available
    engine = ExecutionEngine()
    engine._mcp_available = True
    engine._mcp_check_ts = datetime.now(UTC)

    mock_client = AsyncMock()
    mock_client.get_market_clock.return_value = {"is_open": True}
    mock_client.get_stock_snapshot.return_value = {
        "latestTrade": {"p": 100.0},
        "latestQuote": {"bp": 99.95, "ap": 100.05},
    }
    mock_client._call_tool.return_value = {"id": "order-123", "status": "accepted"}
    engine._mcp_client = mock_client
    engine._get_mcp_client = lambda: mock_client

    # Create Candidate (Needed for signature)
    candidate = CandidateTrade(
        candidate_id="c1",
        ticker="SPY",
        timestamp_utc=datetime.now(UTC),
        rule_id="r1",
        direction="LONG",
        evidence={},
    )

    # Create Decision
    decision = StrategyDecision(
        decision_id="test_cb_1",
        candidate_id="c1",
        timestamp_utc=datetime.now(UTC),
        ticker="SPY",
        strategy_version_id="v1",
        decision="EXECUTE",
        execution_params={"order_type": "MARKET"},
        executed_successfully=None,
    )

    await engine.execute_order(decision, candidate)

    # 4. Assert Blocked
    assert decision.executed_successfully == "FALSE"

    print(f"\nSUCCESS: Trade blocked. Status: {decision.executed_successfully}")


if __name__ == "__main__":
    asyncio.run(test_circuit_breaker_blocks_trade())
