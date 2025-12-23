import os

import pytest

# Must set DB_URL before importing orion.storage.db to avoid eager connection errors
os.environ["DB_URL"] = "sqlite+aiosqlite:///:memory:"

from orion.core.circuit_breaker import CircuitBreaker


@pytest.mark.asyncio
async def test_circuit_breaker_flow():
    # Setup DB is handled by conftest or we do it manual if needed.
    # Assuming conftest sets up the DB engine for us if we use aiosqlite memory logic
    # But let's be safe and init tables.
    from orion.storage.db import Base, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    cb = CircuitBreaker()

    # 1. Initially Closed (or non-existent)
    assert await cb.is_open() is False

    # 2. Open it
    await cb.open("Test Failure")
    assert await cb.is_open() is True

    state = await cb.get_state()
    assert state["status"] == "OPEN"
    assert state["reason"] == "Test Failure"

    # 3. Close it
    await cb.close()
    assert await cb.is_open() is False

    state = await cb.get_state()
    assert state["status"] == "CLOSED"
