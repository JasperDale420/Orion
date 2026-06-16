"""Tests for the degraded-discovery health gate.

H-09 from predict 260430-1751-execution-reliability flagged that when ticker
discovery falls back to the static SPY/QQQ/... list, the rest of the pipeline
keeps emitting candidates and execution proceeds — only a log warning fires
after the streak crosses the threshold. Until this fix, the warning was the
ONLY operator-visible signal; nothing in the execution path conditioned on
the discovery source.

The fix:
  1. `feature_enrichment` writes DEGRADED to SystemStatus[degraded_discovery]
     when streak >= warn_streak, OK otherwise. Updates every cycle so the
     row's last_updated_utc doubles as a liveness signal.
  2. `ExecutionEngine._check_system_health` reads the row and rejects trades
     while DEGRADED.
"""

from __future__ import annotations

import os

import pytest

# Force in-memory SQLite for unit tests
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///:memory:")

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from sqlalchemy import select

from orion.enrichment.heber_context import (
    DEGRADED_DISCOVERY_KEY,
    DISCOVERY_STATUS_DEGRADED,
    DISCOVERY_STATUS_OK,
    _is_discovery_degraded,
    persist_discovery_status,
)
from orion.storage.db import async_session_factory, init_db
from orion.storage.models import SystemStatus


@pytest.mark.unit
class TestDiscoveryDegradationLogic:
    """Pure-logic predicate test — no DB needed."""

    def test_bronze_db_is_never_degraded(self) -> None:
        assert _is_discovery_degraded("bronze_db", streak=0, warn_streak=3) is False
        assert _is_discovery_degraded("bronze_db", streak=99, warn_streak=3) is False

    def test_heber_is_never_degraded(self) -> None:
        assert _is_discovery_degraded("heber", streak=0, warn_streak=3) is False
        assert _is_discovery_degraded("heber", streak=99, warn_streak=3) is False

    def test_static_fallback_below_threshold_is_not_degraded(self) -> None:
        assert _is_discovery_degraded("static_fallback", streak=1, warn_streak=3) is False
        assert _is_discovery_degraded("static_fallback", streak=2, warn_streak=3) is False

    def test_static_fallback_at_or_above_threshold_is_degraded(self) -> None:
        assert _is_discovery_degraded("static_fallback", streak=3, warn_streak=3) is True
        assert _is_discovery_degraded("static_fallback", streak=10, warn_streak=3) is True

    def test_unknown_source_is_treated_as_degraded_at_threshold(self) -> None:
        # Defensive — anything that's not bronze/heber is suspect.
        assert _is_discovery_degraded("???", streak=3, warn_streak=3) is True


@pytest.mark.integration
@pytest.mark.asyncio
class TestPersistDiscoveryStatus:
    async def test_writes_ok_when_source_healthy(self) -> None:
        await init_db()
        await persist_discovery_status(source="bronze_db", streak=0, warn_streak=3)

        async with async_session_factory() as session:
            stmt = select(SystemStatus).where(SystemStatus.key == DEGRADED_DISCOVERY_KEY)
            row = (await session.execute(stmt)).scalars().first()

        assert row is not None
        assert row.status == DISCOVERY_STATUS_OK
        assert "source=bronze_db" in row.details

    async def test_writes_degraded_when_static_streak_threshold_crossed(self) -> None:
        await init_db()
        await persist_discovery_status(source="static_fallback", streak=3, warn_streak=3)

        async with async_session_factory() as session:
            stmt = select(SystemStatus).where(SystemStatus.key == DEGRADED_DISCOVERY_KEY)
            row = (await session.execute(stmt)).scalars().first()

        assert row is not None
        assert row.status == DISCOVERY_STATUS_DEGRADED

    async def test_writes_ok_when_static_streak_below_threshold(self) -> None:
        await init_db()
        await persist_discovery_status(source="static_fallback", streak=2, warn_streak=3)

        async with async_session_factory() as session:
            stmt = select(SystemStatus).where(SystemStatus.key == DEGRADED_DISCOVERY_KEY)
            row = (await session.execute(stmt)).scalars().first()

        assert row is not None
        assert row.status == DISCOVERY_STATUS_OK

    async def test_round_trip_through_recovery(self) -> None:
        """Flag flips DEGRADED → OK when source recovers."""
        await init_db()

        # Degrade
        await persist_discovery_status(source="static_fallback", streak=5, warn_streak=3)
        async with async_session_factory() as session:
            stmt = select(SystemStatus).where(SystemStatus.key == DEGRADED_DISCOVERY_KEY)
            row = (await session.execute(stmt)).scalars().first()
        assert row.status == DISCOVERY_STATUS_DEGRADED

        # Recover
        await persist_discovery_status(source="bronze_db", streak=0, warn_streak=3)
        async with async_session_factory() as session:
            stmt = select(SystemStatus).where(SystemStatus.key == DEGRADED_DISCOVERY_KEY)
            row = (await session.execute(stmt)).scalars().first()
        assert row.status == DISCOVERY_STATUS_OK

    async def test_last_updated_utc_advances_each_call(self) -> None:
        """Liveness: the row's last_updated_utc must update on every call so
        downstream consumers can detect a stuck feature_enrichment service."""
        await init_db()
        await persist_discovery_status(source="bronze_db", streak=0, warn_streak=3)
        async with async_session_factory() as session:
            stmt = select(SystemStatus).where(SystemStatus.key == DEGRADED_DISCOVERY_KEY)
            row = (await session.execute(stmt)).scalars().first()
        first_ts = row.last_updated_utc

        # Tiny delay so wall-clock advances meaningfully
        import asyncio

        await asyncio.sleep(0.01)

        await persist_discovery_status(source="bronze_db", streak=0, warn_streak=3)
        async with async_session_factory() as session:
            stmt = select(SystemStatus).where(SystemStatus.key == DEGRADED_DISCOVERY_KEY)
            row = (await session.execute(stmt)).scalars().first()
        second_ts = row.last_updated_utc

        assert second_ts >= first_ts


@pytest.mark.integration
@pytest.mark.asyncio
class TestExecutionEngineHealthGate:
    """ExecutionEngine._check_system_health must consult the discovery flag."""

    async def _setup_healthy_baseline(self) -> None:
        """Seed a HEALTHY global_health row so health checks pass on baseline."""
        await init_db()
        async with async_session_factory() as session:
            session.add(
                SystemStatus(
                    key="global_health",
                    status="HEALTHY",
                    details="seeded by test",
                    last_updated_utc=datetime.now(UTC),
                )
            )
            await session.commit()

    async def test_execution_blocked_when_discovery_degraded(self) -> None:
        from orion.execution.execution_engine import ExecutionEngine

        await self._setup_healthy_baseline()
        await persist_discovery_status(source="static_fallback", streak=5, warn_streak=3)

        engine = ExecutionEngine()
        engine._gateway_available = True
        engine._gateway_check_ts = datetime.now(UTC)
        # Bypass the cache so we read the freshly-persisted DEGRADED row
        engine._health_cache = None

        assert await engine._check_system_health() is False

    async def test_execution_allowed_when_discovery_recovered(self) -> None:
        from orion.execution.execution_engine import ExecutionEngine

        await self._setup_healthy_baseline()
        # Set degraded then recover
        await persist_discovery_status(source="static_fallback", streak=5, warn_streak=3)
        await persist_discovery_status(source="bronze_db", streak=0, warn_streak=3)

        engine = ExecutionEngine()
        engine._gateway_available = True
        engine._gateway_check_ts = datetime.now(UTC)
        engine._health_cache = None

        assert await engine._check_system_health() is True

    async def test_execution_allowed_when_no_discovery_row_exists(self) -> None:
        """Backward compat: pre-fix deployments have no degraded_discovery row.
        Execution must still be allowed in that case (no row = treat as OK)."""
        from orion.execution.execution_engine import ExecutionEngine

        await self._setup_healthy_baseline()
        # Explicitly delete any degraded_discovery row that prior tests may have created
        async with async_session_factory() as session:
            stmt = select(SystemStatus).where(SystemStatus.key == DEGRADED_DISCOVERY_KEY)
            existing = (await session.execute(stmt)).scalars().first()
            if existing:
                await session.delete(existing)
                await session.commit()

        engine = ExecutionEngine()
        engine._gateway_available = True
        engine._gateway_check_ts = datetime.now(UTC)
        engine._health_cache = None

        assert await engine._check_system_health() is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_persist_discovery_status_does_not_raise_on_db_failure() -> None:
    """The enrichment loop must not crash if the discovery-status write fails —
    a DB hiccup should not take down the polling service."""
    from unittest.mock import patch

    # Patch async_session_factory to simulate a write failure
    fake_factory = AsyncMock(side_effect=RuntimeError("simulated DB failure"))
    with patch("orion.enrichment.heber_context.async_session_factory", fake_factory):
        # Should NOT raise
        await persist_discovery_status(source="bronze_db", streak=0, warn_streak=3)
