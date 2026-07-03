"""The conftest autouse teardown must never ``drop_all`` a real database.

Guards the 2026-06-30 production solver-table wipe: an e2e test that left
``db.engine`` pointed at the real TimescaleDB must not have its tables dropped by
the in-memory test teardown. Only the in-memory SQLite engine is drop-eligible.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import create_async_engine

from _db_safety import is_in_memory_test_engine


def test_in_memory_sqlite_is_a_test_engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    assert is_in_memory_test_engine(eng) is True


def test_file_sqlite_is_not_a_test_engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'real.db'}")
    assert is_in_memory_test_engine(eng) is False


def test_real_timescaledb_is_not_a_test_engine():
    # The real DB the e2e tests point at — must never be drop_all'd by teardown.
    eng = create_async_engine(
        "postgresql+asyncpg://orion:x@localhost:5440/orion_db"  # pragma: allowlist secret
    )
    assert is_in_memory_test_engine(eng) is False


def test_none_engine_is_not_a_test_engine():
    assert is_in_memory_test_engine(None) is False
