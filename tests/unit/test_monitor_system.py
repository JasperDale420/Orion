import os

# Set DB for testing BEFORE imports to avoid loading Postgres config
os.environ["DB_URL"] = "sqlite+aiosqlite:///:memory:"

import logging
from datetime import datetime, timedelta, timezone

import pytest
from orion.jobs.monitor_system import check_dlq, check_heartbeats
from orion.storage.models import SystemStatus
from orion.storage.models_dlq import DeadLetterQueue


@pytest.mark.asyncio
async def test_monitor_logic(caplog, capsys):
    """
    Verify monitor detects:
    1. Stale Heartbeats
    2. New DLQ Failures
    """
    from orion.storage.db import Base, async_session_factory, engine

    # Setup DB
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_factory() as session:
        # Seed 1: Healthy Heartbeat
        h1 = SystemStatus(key="healthy_service", status="HEALTHY", last_updated_utc=datetime.now(timezone.utc))

        # Seed 2: Stale Heartbeat (10 mins ago)
        # SQLAlchemy server default func.now() might be tricky in sqlite memory if we don't set explicit
        stale_ts = datetime.now(timezone.utc) - timedelta(minutes=10)
        h2 = SystemStatus(key="stale_service", status="HEALTHY", last_updated_utc=stale_ts)

        # Seed 3: DLQ Entry (Recent)
        dlq1 = DeadLetterQueue(error_message="Test Error", status="FAILED", timestamp_utc=datetime.now(timezone.utc))

        session.add_all([h1, h2, dlq1])
        await session.commit()

    # Run Checks
    async with async_session_factory() as session:
        with caplog.at_level(logging.INFO):
            await check_heartbeats(session)
            await check_dlq(session)

    # Verify emitted structured logs (stdout JSON in this project).
    out = capsys.readouterr().out
    assert "ALERT: Stale Heartbeat for stale_service" in out
    assert "Heartbeat OK: healthy_service" in out
    assert "ALERT: 1 new Failures in DLQ" in out
