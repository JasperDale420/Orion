"""Tests for PositionMonitor.sync_positions preserving entry_time on restart.

Regression coverage for H-07 (predict 260430-1751-execution-reliability):
when the monitor sees a broker position for the first time (e.g. after a
restart), the ML exit classifier feature `time_held_hours` is computed from
`entry_time`. Previously this was approximated as `datetime.now(UTC)` for
every synced position, biasing ML predictions toward "hold longer" — the
real entry timestamp lives in `strategy_decisions.timestamp_utc` and is
already fetched by `_fetch_entry_context`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from orion.execution.position_monitor import PositionMonitor


@pytest.mark.asyncio
async def test_sync_positions_uses_decision_timestamp_for_new_position() -> None:
    """When a synced position has a matching strategy decision, entry_time = decision_ts."""
    monitor = PositionMonitor()

    real_entry_ts = datetime.now(UTC) - timedelta(hours=4)

    async def _fake_entry_context(symbol: str) -> dict:
        return {
            "decision_id": "dec-real",
            "option_symbol": "AAPL260418C00150000",
            "premium_usd": 200.0,
            "dte": 7,
            "bucket": "SWING",
            "direction": "LONG",
            "entry_time": real_entry_ts,
        }

    monitor._fetch_entry_context = _fake_entry_context  # type: ignore[method-assign]

    connector = SimpleNamespace(
        get_all_positions=lambda: [
            SimpleNamespace(
                symbol="AAPL",
                current_price=2.50,
                avg_entry_price=2.00,
                qty=10.0,
                unrealized_plpc=0.25,
            )
        ]
    )

    positions = await monitor.sync_positions(connector)

    assert len(positions) == 1
    assert positions[0].entry_time == real_entry_ts


@pytest.mark.asyncio
async def test_sync_positions_falls_back_to_now_when_no_decision_row() -> None:
    """Without a matching strategy decision, entry_time falls back to now()."""
    monitor = PositionMonitor()

    async def _no_context(symbol: str) -> dict:
        return {"bucket": "SWING"}  # the default-no-row return

    monitor._fetch_entry_context = _no_context  # type: ignore[method-assign]

    connector = SimpleNamespace(
        get_all_positions=lambda: [
            SimpleNamespace(
                symbol="MSFT",
                current_price=3.00,
                avg_entry_price=2.50,
                qty=5.0,
                unrealized_plpc=0.20,
            )
        ]
    )

    before = datetime.now(UTC)
    positions = await monitor.sync_positions(connector)
    after = datetime.now(UTC)

    assert len(positions) == 1
    # Falls back to now(); should be within the test's wall-clock window.
    assert before <= positions[0].entry_time <= after


@pytest.mark.asyncio
async def test_sync_positions_falls_back_to_now_when_entry_time_is_none() -> None:
    """`entry_time` key present but None should still fall back to now() (not crash)."""
    monitor = PositionMonitor()

    async def _none_ts(symbol: str) -> dict:
        return {
            "decision_id": "dec-1",
            "bucket": "SWING",
            "direction": "LONG",
            "entry_time": None,
        }

    monitor._fetch_entry_context = _none_ts  # type: ignore[method-assign]

    connector = SimpleNamespace(
        get_all_positions=lambda: [
            SimpleNamespace(
                symbol="TSLA",
                current_price=5.00,
                avg_entry_price=4.00,
                qty=2.0,
                unrealized_plpc=0.25,
            )
        ]
    )

    before = datetime.now(UTC)
    positions = await monitor.sync_positions(connector)
    after = datetime.now(UTC)

    assert len(positions) == 1
    assert before <= positions[0].entry_time <= after
