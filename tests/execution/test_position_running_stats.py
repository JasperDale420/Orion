"""Tests for the per-position running-stats persistence (Phase 3
of exit-pipeline RCA, Bug #3).

Before this fix, `TrackedPosition.max_return_pct` / `max_drawdown_pct`
were in-memory only. After a `orion_position_monitor` restart, a
position that had hit +200% and was now at +50% looked to the ML
exit classifier like its `max_return_so_far` was +50% (no peak)
— a fresh stable-trade feature vector that never crossed the
0.55 exit threshold.

These tests pin:
  1. `upsert_position_running_stats` inserts a fresh row.
  2. A second call for the same symbol updates the row.
  3. `load_position_running_stats` returns None for an unknown symbol.
  4. `load_position_running_stats` returns the persisted tuple.
  5. The integration: after a "restart" (drop the in-memory tracker
     dict but keep the DB), a re-tracked position has its
     max_return_pct seeded from the persisted value, not from
     `max(0, unrealized)`.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///:memory:")

from sqlalchemy import select  # noqa: E402

from orion.execution.persistence import (  # noqa: E402
    load_position_running_stats,
    upsert_position_running_stats,
)
from orion.storage.db import async_session_factory, init_db  # noqa: E402


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upsert_inserts_new_row() -> None:
    from orion.storage.models_execution import PositionRunningStats

    await init_db()
    await upsert_position_running_stats(
        symbol="UPSERT_TEST_NEW",
        max_return_pct=125.5,
        max_drawdown_pct=-42.0,
    )

    async with async_session_factory() as session:
        row = (
            (
                await session.execute(
                    select(PositionRunningStats).where(PositionRunningStats.symbol == "UPSERT_TEST_NEW")
                )
            )
            .scalars()
            .first()
        )

    assert row is not None
    assert row.max_return_pct == 125.5
    assert row.max_drawdown_pct == -42.0
    assert row.last_updated_utc is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upsert_updates_existing_row() -> None:
    from orion.storage.models_execution import PositionRunningStats

    await init_db()

    await upsert_position_running_stats(
        symbol="UPSERT_TEST_EXISTING",
        max_return_pct=50.0,
        max_drawdown_pct=-10.0,
    )

    # Update with new peak
    await upsert_position_running_stats(
        symbol="UPSERT_TEST_EXISTING",
        max_return_pct=200.0,
        max_drawdown_pct=-25.0,
    )

    async with async_session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(PositionRunningStats).where(PositionRunningStats.symbol == "UPSERT_TEST_EXISTING")
                )
            )
            .scalars()
            .all()
        )

    assert len(rows) == 1, "must update in place, not insert duplicate"
    assert rows[0].max_return_pct == 200.0
    assert rows[0].max_drawdown_pct == -25.0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_load_returns_none_for_unknown_symbol() -> None:
    await init_db()
    result = await load_position_running_stats("UNKNOWN_TICKER_XYZZY")
    assert result is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_load_returns_persisted_tuple() -> None:
    await init_db()

    await upsert_position_running_stats(
        symbol="LOAD_TEST",
        max_return_pct=88.8,
        max_drawdown_pct=-12.5,
    )

    result = await load_position_running_stats("LOAD_TEST")
    assert result is not None
    max_ret, max_dd = result
    assert max_ret == 88.8
    assert max_dd == -12.5


@pytest.mark.integration
@pytest.mark.asyncio
async def test_swallows_db_errors_silently() -> None:
    """A DB blip during upsert must NOT propagate — it would crash
    the every-5-second sync_positions loop across ~50 positions."""
    from unittest.mock import MagicMock

    await init_db()

    # Force db_write to raise. The function should log and return
    # without re-raising.
    fake = AsyncMock(side_effect=RuntimeError("simulated DB failure"))
    with patch("orion.execution.persistence.db_write", fake):
        await upsert_position_running_stats(
            symbol="ERROR_TEST",
            max_return_pct=10.0,
            max_drawdown_pct=-5.0,
        )  # MUST NOT raise

    # And likewise for load
    fake_query = AsyncMock(side_effect=RuntimeError("simulated DB failure"))
    with patch("orion.execution.persistence.db_query", fake_query):
        result = await load_position_running_stats("ERROR_TEST")
    assert result is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rehydration_seeds_max_return_from_persisted() -> None:
    """End-to-end: a TrackedPosition created via sync_positions for a
    symbol that has a persisted running-stats row must inherit the
    persisted max_return_pct, not the fresh `max(0, unrealized)` seed.

    Simulates the "restart" scenario that motivated Bug #3.
    """
    from datetime import UTC, datetime

    from orion.execution.position_monitor import PositionMonitor, TrackedPosition  # noqa: F401

    await init_db()

    # Plant a persisted row that represents a position that had hit
    # +250% and dipped to -10% earlier in the session.
    await upsert_position_running_stats(
        symbol="REHYDRATE_TEST_AAPL_OPTION",
        max_return_pct=250.0,
        max_drawdown_pct=-10.0,
    )

    monitor = PositionMonitor()
    # Empty tracker — simulates fresh container.
    assert monitor.tracked_positions == {}

    # Fake broker position currently showing only +50% unrealized.
    # If the rehydration is broken, the new TrackedPosition will have
    # max_return_pct=50 (a fresh seed from max(0, 50)). With Phase 3
    # working, it should be 250 (from the persisted row).
    fake_broker_pos = SimpleNamespace(
        symbol="REHYDRATE_TEST_AAPL_OPTION",
        current_price=3.50,
        avg_entry_price=2.33,  # so unrealized ≈ +50%
        qty=10,
        unrealized_plpc=0.50,  # 50%
    )

    fake_connector = SimpleNamespace(
        get_all_positions=lambda: [fake_broker_pos],
    )

    # Stub _fetch_entry_context so we don't hit decision-trace DB
    # joins — they're not the subject of this test.
    monitor._fetch_entry_context = AsyncMock(
        return_value={
            "bucket": "SWING",
            "direction": "LONG",
            "entry_time": datetime.now(UTC),
        }
    )

    result = await monitor.sync_positions(fake_connector)

    assert len(result) == 1
    pos = result[0]
    assert pos.symbol == "REHYDRATE_TEST_AAPL_OPTION"
    # The key assertion: the rehydrated max_return_pct is the
    # persisted 250, not the fresh max(0, 50) = 50.
    assert pos.max_return_pct == 250.0, (
        f"max_return_pct must rehydrate from persisted row, got {pos.max_return_pct} "
        f"(rehydration broken — ML branch will see fresh-position feature vector "
        f"and never cross exit threshold)"
    )
    assert pos.max_drawdown_pct == -10.0, (
        f"max_drawdown_pct must rehydrate from persisted row, got {pos.max_drawdown_pct}"
    )
