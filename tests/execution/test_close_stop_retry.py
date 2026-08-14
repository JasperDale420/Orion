"""position_monitor stops hammering a position that repeatedly fails to close.

Backstop for the 2026-05-29 flood: even with the reduce-only guard, a position
that keeps failing to close (e.g. a genuinely stuck contract) must not fire an
unbounded stream of attempts every 60s. After N consecutive failures the symbol
is abandoned (with a CRITICAL alert) until a close succeeds, the abandon
cooldown elapses, or the process restarts. The cooldown keeps abandonment from
being permanent — a transient cause (stale mark, buying-power wall, sibling's
resting order) must not strand a position forever (RCA 2026-06-05: MU).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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


def test_abandoned_symbol_retries_after_cooldown(monkeypatch):
    """Abandonment must be time-bounded: once the cooldown elapses since the
    last failure, the symbol is eligible again and the counter resets. Without
    this a +320% MU winner stayed unclosable until process restart
    (RCA 2026-06-05)."""
    pm = PositionMonitor(execution_engine=MagicMock(), position_manager=PositionManager())
    sym = "MU260612P00790000"
    clock = {"t": 1000.0}
    monkeypatch.setattr(pm, "_now", lambda: clock["t"])

    for _ in range(pm._MAX_CONSECUTIVE_CLOSE_FAILURES):
        pm._record_close_result(sym, success=False)
    # Just abandoned — cooldown not yet elapsed.
    assert pm._close_attempts_exhausted(sym) is True

    # Advance past the cooldown → eligible again, counter reset.
    clock["t"] += pm._CLOSE_ABANDON_COOLDOWN_SECONDS + 1.0
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


# ── Expiry flatten: the cooldown must not outlast the session ──────────────


def _zero_dte_pos(symbol="SPY260703C00560000", expiry=None):
    pos = _pos(symbol)
    pos.bucket = "0DTE"
    pos.expiry_date = expiry
    return pos


def test_flatten_retries_on_the_short_cooldown(monkeypatch):
    """A 0DTE flatten fires 15 minutes before the close; the standard 10-minute
    abandon cooldown would eat that whole window and let an ITM contract reach
    expiry. An expiry flatten therefore waits ~a cycle, not ten minutes — while
    an ordinary close on the same symbol still serves the full cooldown."""
    pm = PositionMonitor(execution_engine=MagicMock(), position_manager=PositionManager())
    sym = "SPY260703C00560000"
    clock = {"t": 1000.0}
    monkeypatch.setattr(pm, "_now", lambda: clock["t"])

    for _ in range(pm._MAX_CONSECUTIVE_CLOSE_FAILURES):
        pm._record_close_result(sym, success=False)
    assert pm._close_attempts_exhausted(sym, expiry_deadline=True) is True

    clock["t"] += pm._FLATTEN_ABANDON_COOLDOWN_SECONDS + 1.0
    # Not yet eligible as an ordinary close...
    assert pm._close_attempts_exhausted(sym) is True
    # ...but a flatten gets its next attempt inside the session.
    assert pm._close_attempts_exhausted(sym, expiry_deadline=True) is False
    assert sym not in pm._consecutive_close_failures


@pytest.mark.asyncio
async def test_execute_exits_uses_short_cooldown_for_expiring_position(monkeypatch):
    """Wiring: the POSITION expiring today is what selects the short cooldown.

    Keying off the winning rule id would miss this case — the fallback rules
    rank stop-loss and profit-target above the flatten, so a 0DTE past its
    cutoff usually arrives labelled something else while racing the same
    expiry. Here the prediction is a stop-loss and the short cooldown must
    still apply.
    """
    from types import SimpleNamespace

    engine = MagicMock()
    engine.close_position = AsyncMock(return_value=False)
    monitor = PositionMonitor(execution_engine=engine, position_manager=PositionManager())
    clock = {"t": 1000.0}
    monkeypatch.setattr(monitor, "_now", lambda: clock["t"])

    flatten = SimpleNamespace(
        should_exit=True,
        confidence=1.0,
        reasoning="stop loss hit",
        rule_id="stop_loss_v1",
    )
    connector = MagicMock()
    with patch("orion.ml.performance_tracker.log_exit_prediction", new_callable=AsyncMock):
        with patch("orion.ml.performance_tracker.log_outcome", new_callable=AsyncMock):
            for _ in range(monitor._MAX_CONSECUTIVE_CLOSE_FAILURES):
                await monitor.execute_exits(connector, [(_zero_dte_pos(), flatten)], dry_run=False)
            calls_at_threshold = engine.close_position.call_count

            # Past the flatten cooldown but well inside the standard one.
            clock["t"] += monitor._FLATTEN_ABANDON_COOLDOWN_SECONDS + 1.0
            await monitor.execute_exits(connector, [(_zero_dte_pos(), flatten)], dry_run=False)

    assert engine.close_position.call_count == calls_at_threshold + 1


def test_already_expired_position_keeps_the_standard_cooldown(monkeypatch):
    """A row still tracked after its expiry is stale, not racing a deadline —
    it must not sit on the 60s retry forever burning Gateway calls."""
    pm = PositionMonitor(execution_engine=MagicMock(), position_manager=PositionManager())
    sym = "SPY260703C00560000"
    clock = {"t": 1000.0}
    monkeypatch.setattr(pm, "_now", lambda: clock["t"])

    from orion.execution.exit_fallback_rules import racing_expiry

    stale = _zero_dte_pos(sym, expiry=datetime.now(UTC) - timedelta(days=3))
    assert racing_expiry(stale) is False
    # ...while a 0DTE whose expiry was never populated still gets the short one:
    # that missing-expiry case is exactly how a position reaches expiry unexited.
    assert racing_expiry(_zero_dte_pos(sym)) is True

    for _ in range(pm._MAX_CONSECUTIVE_CLOSE_FAILURES):
        pm._record_close_result(sym, success=False)
    clock["t"] += pm._FLATTEN_ABANDON_COOLDOWN_SECONDS + 1.0
    assert pm._close_attempts_exhausted(sym, expiry_deadline=racing_expiry(stale)) is True
