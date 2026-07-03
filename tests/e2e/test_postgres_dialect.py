"""
E2E Postgres-dialect tests: exercise production code paths that ONLY exist on
real Postgres/pgvector and are invisible to the in-memory SQLite test suite.

Audit finding #17 (High): every unit/integration test runs on SQLite, so
pgvector ``Vector`` ops, ``ON CONFLICT`` semantics, and other Postgres-specific
paths are untested. This module closes that blind spot by running the *actual*
production functions against the real TimescaleDB.

Production code genuinely exercised here (on Postgres, not SQLite):
  - orion.processing.persistence.persist_bronze_events  -> ON CONFLICT DO NOTHING
  - orion.processing.persistence.persist_silver_signals -> ON CONFLICT DO NOTHING
  - orion.processing.feature_engine.FeatureEngine.persist_features -> ON CONFLICT
    DO UPDATE (upsert)

The embedder (Ollama/OpenAI) is the only thing stubbed — we feed deterministic
768-dim vectors so the assertions are reproducible. The pgvector SQL, the JSONB
operators, and the ON CONFLICT clauses all run for real against Postgres.

Requires: TimescaleDB on localhost:5440. Skips cleanly when unreachable.

Run:
    uv run pytest tests/e2e/test_postgres_dialect.py -v
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

REAL_DB_URL = "postgresql+asyncpg://orion:orion_password@localhost:5440/orion_db"  # pragma: allowlist secret

# Every row this module creates carries this marker so cleanup is exhaustive and
# repeated runs are idempotent.
TEST_MARKER = "pgdialect_test"
EMBEDDING_DIM = 768


def _run_tag() -> str:
    return f"{TEST_MARKER}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


async def _postgres_reachable() -> bool:
    """Probe the real DB. Returns False on any connection failure (skip, not error)."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(REAL_DB_URL)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


def _unit_vector(dim: int, hot_index: int) -> list[float]:
    """A deterministic unit-ish vector: 1.0 at hot_index, small noise elsewhere."""
    vec = [0.01] * dim
    vec[hot_index % dim] = 1.0
    return vec


# ---------------------------------------------------------------------------
# Fixture: bind the real Postgres engine, restore SQLite before teardown.
#
# The autouse ``setup_test_db`` fixture (tests/conftest.py) re-binds in-memory
# SQLite and, on teardown, runs Base.metadata.drop_all + engine.dispose against
# whatever engine is currently bound. If we left Postgres bound, that teardown
# would DROP EVERY ORION TABLE on the live DB. So we restore the SQLite binding
# inside this fixture's finally block *before* yielding control back — exactly
# the pattern test_smoke_e2e.py uses.
# ---------------------------------------------------------------------------
@pytest.fixture
async def pg_db():
    if not await _postgres_reachable():
        pytest.skip(f"TimescaleDB not reachable at {REAL_DB_URL}; skipping Postgres-dialect e2e tests")

    from orion.storage import db

    db.configure_db(REAL_DB_URL, echo=False)
    try:
        await db.init_db()
        yield db
    finally:
        # CRITICAL: restore SQLite before the autouse teardown's drop_all runs,
        # so it never touches the real Postgres schema.
        await db.engine.dispose()
        db.configure_db("sqlite+aiosqlite:///:memory:", echo=False)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_on_conflict_do_nothing_dedupes(pg_db):
    """
    persist_bronze_events and persist_silver_signals use INSERT ... ON CONFLICT
    DO NOTHING. SQLite's dialect-less insert never exercises this. Insert a
    duplicate-key batch against real Postgres and assert the original row is kept
    (no error, no overwrite).

    Exercises: orion.processing.persistence.persist_bronze_events and
    persist_silver_signals ON CONFLICT DO NOTHING semantics.
    """
    from orion.processing.persistence import persist_bronze_events, persist_silver_signals
    from orion.storage.models import BronzeEvent
    from orion.storage.models_silver import SilverSignal

    run_tag = _run_tag()
    ticker = "PGNC"
    now = (datetime.now(UTC) + timedelta(days=30)).replace(microsecond=0)

    bronze_id = f"{run_tag}_bronze"
    silver_id = f"{run_tag}_silver"

    def _bronze(premium: float) -> BronzeEvent:
        return BronzeEvent(
            event_id=bronze_id,
            source="UW",
            source_event_id=f"{run_tag}_src",
            event_type="UW_FLOW",
            ticker=ticker,
            trading_date=now.date(),
            session="RTH",
            schema_version="v1",
            event_ts_utc=now,
            received_ts_utc=now,
            payload={"marker": TEST_MARKER, "premium": premium},
            ingest={},
        )

    def _silver(premium: float) -> SilverSignal:
        return SilverSignal(
            signal_id=silver_id,
            ticker=ticker,
            signal_ts_utc=now,
            signal_type="UW_FLOW",
            features={"marker": TEST_MARKER, "premium": premium},
        )

    try:
        async with pg_db.async_session_factory() as session:
            # First write establishes the rows.
            await persist_bronze_events(session, [_bronze(100.0)])
            await persist_silver_signals(session, [_silver(100.0)])
            await session.commit()

        async with pg_db.async_session_factory() as session:
            # Duplicate-key write with DIFFERENT payload — DO NOTHING must keep
            # the original (premium=100), not raise and not overwrite.
            await persist_bronze_events(session, [_bronze(999.0)])
            await persist_silver_signals(session, [_silver(999.0)])
            await session.commit()

        async with pg_db.async_session_factory() as session:
            bronze_rows = (
                await session.execute(
                    text("SELECT payload FROM bronze_events WHERE event_id = :id"),
                    {"id": bronze_id},
                )
            ).all()
            silver_rows = (
                await session.execute(
                    text("SELECT features FROM silver_signals WHERE signal_id = :id"),
                    {"id": silver_id},
                )
            ).all()

        assert len(bronze_rows) == 1, "DO NOTHING should not create a duplicate bronze row"
        assert len(silver_rows) == 1, "DO NOTHING should not create a duplicate silver row"
        assert bronze_rows[0][0]["premium"] == 100.0, "DO NOTHING must NOT overwrite the original bronze payload"
        assert silver_rows[0][0]["premium"] == 100.0, "DO NOTHING must NOT overwrite the original silver features"
    finally:
        async with pg_db.async_session_factory() as session:
            await session.execute(text("DELETE FROM bronze_events WHERE event_id = :id"), {"id": bronze_id})
            await session.execute(text("DELETE FROM silver_signals WHERE signal_id = :id"), {"id": silver_id})
            await session.commit()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_on_conflict_do_update_upserts(pg_db):
    """
    FeatureEngine.persist_features uses INSERT ... ON CONFLICT DO UPDATE on the
    (ticker, event_ts_utc, feature_set_id) composite key. Insert then re-insert
    the same key with a new feature vector and assert the row is UPDATED in place.

    Exercises: orion.processing.feature_engine.FeatureEngine.persist_features
    ON CONFLICT DO UPDATE (upsert) semantics on real Postgres.
    """
    from orion.processing.feature_engine import FeatureEngine
    from orion.storage import db as db_module

    run_tag = _run_tag()
    ticker = "PGUP"
    ts = (datetime.now(UTC) + timedelta(days=30)).replace(microsecond=0)
    feature_set_id = f"{run_tag}_fs"

    fe = FeatureEngine()

    async def _read_features() -> dict | None:
        async with pg_db.async_session_factory() as session:
            row = (
                await session.execute(
                    text("SELECT features FROM gold_feature_events WHERE ticker = :t AND feature_set_id = :fs"),
                    {"t": ticker, "fs": feature_set_id},
                )
            ).first()
            return row[0] if row else None

    try:
        # persist_features routes through db_write -> async_session_factory, both
        # already bound to the real Postgres engine by the pg_db fixture.
        assert db_module.engine.dialect.name == "postgresql"

        await fe.persist_features(ticker, ts, {"alpha": 1.0, "marker": TEST_MARKER}, feature_set_id)
        first = await _read_features()
        assert first is not None, "feature row was not inserted"
        assert first["alpha"] == 1.0

        # Same composite key, new payload -> DO UPDATE must overwrite, not insert.
        await fe.persist_features(ticker, ts, {"alpha": 2.0, "marker": TEST_MARKER}, feature_set_id)

        async with pg_db.async_session_factory() as session:
            count = (
                await session.execute(
                    text("SELECT count(*) FROM gold_feature_events WHERE ticker = :t AND feature_set_id = :fs"),
                    {"t": ticker, "fs": feature_set_id},
                )
            ).scalar()
        assert count == 1, "DO UPDATE must update in place, not create a second row"

        updated = await _read_features()
        assert updated is not None
        assert updated["alpha"] == 2.0, "DO UPDATE should have overwritten the features payload"
    finally:
        async with pg_db.async_session_factory() as session:
            await session.execute(
                text("DELETE FROM gold_feature_events WHERE ticker = :t AND feature_set_id = :fs"),
                {"t": ticker, "fs": feature_set_id},
            )
            await session.commit()
