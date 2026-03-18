import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import insert

from orion.storage.models import SystemStatus
from orion.storage.models_gold import CandidateTrade, StrategyDecision


@pytest.mark.asyncio
async def test_stale_heartbeat_blocks_trade():
    """
    Verifies that ExecutionEngine aborts trade when SystemStatus is HEALTHY but STALE.
    """
    # 1. Setup Environment
    from datetime import timedelta
    from unittest.mock import AsyncMock

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

    import orion.storage.db

    orion.storage.db.engine = test_engine
    orion.storage.db.async_session_factory = test_session_factory

    from orion.storage.db import Base

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    import importlib

    import orion.execution.execution_engine

    importlib.reload(orion.execution.execution_engine)
    from orion.execution.execution_engine import ExecutionEngine

    # 2. Seed STALE Healthy Status
    # Default ingestion_heartbeat_max_age is 70s. We set lag to 90s.
    stale_time = datetime.now(UTC) - timedelta(seconds=90)

    async with test_session_factory() as session:
        await session.execute(
            insert(SystemStatus).values(
                key="global_health", status="HEALTHY", details="Stale Test", last_updated_utc=stale_time
            )
        )
        await session.commit()

    # 3. Attempt Execution with Gateway mocked as available
    engine = ExecutionEngine()
    engine._gateway_available = True
    engine._gateway_check_ts = datetime.now(UTC)

    mock_client = AsyncMock()
    mock_client.get_clock.return_value = {"is_open": True}
    mock_client.get_option_chain.return_value = {"contracts": [{"symbol": "QQQ260418C00400000", "mid": 1.0}]}
    mock_client.create_order.return_value = {"id": "order-123", "status": "accepted"}
    engine._get_gateway_client = lambda: mock_client

    candidate = CandidateTrade(
        candidate_id="c2",
        ticker="QQQ",
        timestamp_utc=datetime.now(UTC),
        rule_id="r1",
        direction="SHORT",
        evidence={},
        option_symbol="QQQ260418C00400000",
        premium=1.0,
    )

    decision = StrategyDecision(
        decision_id="test_cb_stale",
        candidate_id="c2",
        timestamp_utc=datetime.now(UTC),
        ticker="QQQ",
        strategy_version_id="v1",
        decision="EXECUTE",
        execution_params={"order_type": "MARKET"},
        executed_successfully=None,
    )

    await engine.execute_order(decision, candidate)

    # 4. Assert Blocked
    assert decision.executed_successfully == "FALSE"
    print(f"\nSUCCESS: Trade blocked due to Staleness. Status: {decision.executed_successfully}")


if __name__ == "__main__":
    asyncio.run(test_stale_heartbeat_blocks_trade())
