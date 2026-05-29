"""position_monitor stops hammering a position that repeatedly fails to close.

Backstop for the 2026-05-29 flood: even with the reduce-only guard, a position
that keeps failing to close (e.g. a genuinely stuck contract) must not fire an
unbounded stream of attempts every 60s. After N consecutive failures the symbol
is abandoned (with a CRITICAL alert) until a close succeeds or the process
restarts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.execution.position_manager import PositionManager
from orion.execution.position_monitor import PositionMonitor, TrackedPosition
from orion.ml.exit_classifier import ExitPrediction


def _pos(symbol="SMH260605P00550000"):
    return TrackedPosition(
        symbol=symbol,
        qty=10.0,
        entry_price=5.0,
        current_price=4.0,
        unrealized_pnl_pct=-20.0,
        entry_time=datetime.now(UTC),
        bucket="SWING",
        decision_id="d1",
        option_symbol=symbol,
    )


def _pred():
    return ExitPrediction(should_exit=True, confidence=0.9, reasoning="expiry")


async def _run(monitor, engine, n):
    connector = MagicMock()
    with patch("orion.ml.performance_tracker.log_exit_prediction", new_callable=AsyncMock):
        with patch("orion.ml.performance_tracker.log_outcome", new_callable=AsyncMock):
            last = None
            for _ in range(n):
                last = await monitor.execute_exits(connector, [(_pos(), _pred())], dry_run=False)
            return last


# ── Counter logic (unit) ───────────────────────────────────────────────────


def test_counter_exhausts_after_threshold_failures():
    pm = PositionMonitor(execution_engine=MagicMock(), position_manager=PositionManager())
    sym = "SMH260605P00550000"
    assert pm._close_attempts_exhausted(sym) is False
    for _ in range(pm._MAX_CONSECUTIVE_CLOSE_FAILURES):
        pm._record_close_result(sym, success=False)
    assert pm._close_attempts_exhausted(sym) is True


def test_counter_resets_on_success():
    pm = PositionMonitor(execution_engine=MagicMock(), position_manager=PositionManager())
    sym = "SMH260605P00550000"
    for _ in range(pm._MAX_CONSECUTIVE_CLOSE_FAILURES - 1):
        pm._record_close_result(sym, success=False)
    pm._record_close_result(sym, success=True)
    assert pm._close_attempts_exhausted(sym) is False
    assert sym not in pm._consecutive_close_failures


# ── Wiring (integration) ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_exits_abandons_after_repeated_failures():
    engine = MagicMock()
    engine.close_position = AsyncMock(return_value=False)  # always fails
    monitor = PositionMonitor(execution_engine=engine, position_manager=PositionManager())

    await _run(monitor, engine, monitor._MAX_CONSECUTIVE_CLOSE_FAILURES)
    calls_at_threshold = engine.close_position.call_count

    # The next cycle must SKIP submitting (abandoned) — no further close attempt.
    result = await _run(monitor, engine, 1)
    assert engine.close_position.call_count == calls_at_threshold
    assert result[0]["error"] == "close_abandoned_after_repeated_failures"


@pytest.mark.asyncio
async def test_execute_exits_keeps_trying_below_threshold():
    engine = MagicMock()
    engine.close_position = AsyncMock(return_value=False)
    monitor = PositionMonitor(execution_engine=engine, position_manager=PositionManager())

    await _run(monitor, engine, monitor._MAX_CONSECUTIVE_CLOSE_FAILURES - 1)
    # Still under threshold → still attempting each cycle.
    assert engine.close_position.call_count == monitor._MAX_CONSECUTIVE_CLOSE_FAILURES - 1
