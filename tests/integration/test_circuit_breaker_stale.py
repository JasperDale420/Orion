import asyncio
from datetime import datetime

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
    stale_time = datetime.utcnow() - timedelta(seconds=90)

    async with test_session_factory() as session:
        await session.execute(
            insert(SystemStatus).values(
                key="global_health", status="HEALTHY", details="Stale Test", last_updated_utc=stale_time
            )
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
        engine.connector = True
        engine.market_connector = True

    candidate = CandidateTrade(
        candidate_id="c2", ticker="QQQ", timestamp_utc=datetime.utcnow(), rule_id="r1", direction="SHORT", evidence={}
    )

    decision = StrategyDecision(
        decision_id="test_cb_stale",
        candidate_id="c2",
        timestamp_utc=datetime.utcnow(),
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
