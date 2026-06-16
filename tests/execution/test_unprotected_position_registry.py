"""
Tests for unprotected-position tracking (audit #4).

When entry-time bracket placement fails, the position is naked. These tests
verify the first-class plumbing that surfaces it to the risk layer and the
PositionMonitor:

- RiskManager.mark/clear/get_unprotected registry behaviour
- ExecutionEngine registers + Discord-alerts ONCE when a bracket fails
- PositionMonitor re-protects unprotected positions, clearing on success and
  emitting a recovery alert
- The registry NEVER gates order flow (advisory only)
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.core.enums import OrderSide
from orion.execution.position_monitor import PositionMonitor, TrackedPosition
from orion.execution.risk.manager import RiskManager


# ── RiskManager registry ──────────────────────────────────────────────────


def test_risk_registry_mark_get_clear():
    rm = RiskManager()
    assert rm.get_unprotected() == {}

    rm.mark_unprotected("AAPL", "AAPL260418C00150000", "stop_loss: 403")
    snap = rm.get_unprotected()
    assert "AAPL260418C00150000" in snap
    entry = snap["AAPL260418C00150000"]
    assert entry["ticker"] == "AAPL"
    assert entry["reason"] == "stop_loss: 403"
    assert isinstance(entry["marked_at_utc"], datetime)

    rm.clear_unprotected("AAPL260418C00150000")
    assert rm.get_unprotected() == {}


def test_risk_registry_get_returns_copy():
    """Mutating the returned snapshot must not corrupt the live registry."""
    rm = RiskManager()
    rm.mark_unprotected("AAPL", "AAPL260418C00150000", "r")
    snap = rm.get_unprotected()
    snap.clear()
    assert "AAPL260418C00150000" in rm.get_unprotected()


def test_risk_registry_clear_unknown_is_noop():
    rm = RiskManager()
    rm.clear_unprotected("NOTHERE")  # must not raise
    assert rm.get_unprotected() == {}


# ── ExecutionEngine registration + alert ────────────────────────────────────


def _make_engine_with_real_risk():
    from orion.execution.execution_engine import ExecutionEngine

    engine = ExecutionEngine()
    engine.risk_manager = RiskManager()
    return engine


@pytest.mark.asyncio
async def test_register_unprotected_populates_registry_and_alerts_once():
    engine = _make_engine_with_real_risk()
    bracket_result = {
        "unprotected": True,
        "partial_protection": False,
        "failure_reasons": ["stop_loss: 403 wash"],
    }
    with patch("orion.shared.alerts.send_discord_alert", new=AsyncMock(return_value=True)) as alert:
        await engine._register_unprotected_position(
            ticker="AAPL",
            option_symbol="AAPL260418C00150000",
            bracket_result=bracket_result,
        )

    # Risk registry populated.
    snap = engine.risk_manager.get_unprotected()
    assert "AAPL260418C00150000" in snap
    assert "stop_loss: 403 wash" in snap["AAPL260418C00150000"]["reason"]

    # Exactly one alert, deduped per option_symbol.
    alert.assert_awaited_once()
    _, kwargs = alert.await_args
    assert kwargs["dedupe_key"] == "unprotected_AAPL260418C00150000"


@pytest.mark.asyncio
async def test_register_partial_protection_alerts_with_partial_kind():
    engine = _make_engine_with_real_risk()
    bracket_result = {
        "unprotected": False,
        "partial_protection": True,
        "failure_reasons": ["take_profit: timeout"],
    }
    with patch("orion.shared.alerts.send_discord_alert", new=AsyncMock(return_value=True)) as alert:
        await engine._register_unprotected_position(
            ticker="AAPL",
            option_symbol="AAPL260418C00150000",
            bracket_result=bracket_result,
        )
    alert.assert_awaited_once()
    msg = alert.await_args.args[0]
    assert "PARTIALLY PROTECTED" in msg


@pytest.mark.asyncio
async def test_register_alert_failure_does_not_raise():
    """A failed alert must never roll back the entry."""
    engine = _make_engine_with_real_risk()
    with patch(
        "orion.shared.alerts.send_discord_alert",
        new=AsyncMock(side_effect=RuntimeError("discord down")),
    ):
        # Must not raise — registry still populated.
        await engine._register_unprotected_position(
            ticker="AAPL",
            option_symbol="AAPL260418C00150000",
            bracket_result={"unprotected": True, "failure_reasons": []},
        )
    assert "AAPL260418C00150000" in engine.risk_manager.get_unprotected()


# ── ExecutionEngine.reprotect_position ──────────────────────────────────────


@pytest.mark.asyncio
async def test_reprotect_success_clears_registry_and_recovery_alerts():
    engine = _make_engine_with_real_risk()
    engine.risk_manager.mark_unprotected("AAPL", "AAPL260418C00150000", "stop_loss: 403")

    # Both legs now place cleanly.
    engine._place_bracket_orders = AsyncMock(return_value={"unprotected": False, "partial_protection": False})
    with patch("orion.shared.alerts.send_discord_alert", new=AsyncMock(return_value=True)) as alert:
        ok = await engine.reprotect_position(
            ticker="AAPL",
            option_symbol="AAPL260418C00150000",
            entry_price=2.00,
            qty=3,
        )

    assert ok is True
    assert engine.risk_manager.get_unprotected() == {}
    alert.assert_awaited_once()
    assert alert.await_args.kwargs["dedupe_key"] == "reprotected_AAPL260418C00150000"


@pytest.mark.asyncio
async def test_reprotect_still_partial_keeps_registry_and_no_recovery_alert():
    engine = _make_engine_with_real_risk()
    engine.risk_manager.mark_unprotected("AAPL", "AAPL260418C00150000", "stop_loss: 403")

    # Stop still missing → not fully protected.
    engine._place_bracket_orders = AsyncMock(return_value={"unprotected": True, "partial_protection": False})
    with patch("orion.shared.alerts.send_discord_alert", new=AsyncMock(return_value=True)) as alert:
        ok = await engine.reprotect_position(
            ticker="AAPL",
            option_symbol="AAPL260418C00150000",
            entry_price=2.00,
            qty=3,
        )

    assert ok is False
    # Still registered for the next cycle.
    assert "AAPL260418C00150000" in engine.risk_manager.get_unprotected()
    alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_reprotect_bracket_exception_returns_false_and_keeps_registry():
    engine = _make_engine_with_real_risk()
    engine.risk_manager.mark_unprotected("AAPL", "AAPL260418C00150000", "r")
    engine._place_bracket_orders = AsyncMock(side_effect=RuntimeError("gateway down"))

    ok = await engine.reprotect_position(
        ticker="AAPL",
        option_symbol="AAPL260418C00150000",
        entry_price=2.00,
        qty=3,
    )
    assert ok is False
    assert "AAPL260418C00150000" in engine.risk_manager.get_unprotected()


# ── PositionMonitor re-protection (runs FIRST in cycle) ─────────────────────


def _tracked(option_symbol: str, direction: str = "LONG") -> TrackedPosition:
    return TrackedPosition(
        symbol=option_symbol,
        qty=3.0,
        entry_price=2.00,
        current_price=2.10,
        unrealized_pnl_pct=5.0,
        entry_time=datetime.now(UTC),
        bucket="SWING",
        direction=direction,
        option_symbol=option_symbol,
    )


@pytest.mark.asyncio
async def test_monitor_reprotects_tracked_unprotected_position():
    engine = MagicMock()
    engine.risk_manager = RiskManager()
    engine.risk_manager.mark_unprotected("AAPL", "AAPL260418C00150000", "stop_loss: 403")
    engine.reprotect_position = AsyncMock(return_value=True)

    monitor = PositionMonitor(execution_engine=engine)
    monitor.tracked_positions["AAPL260418C00150000"] = _tracked("AAPL260418C00150000")

    await monitor._reprotect_unprotected_positions()

    engine.reprotect_position.assert_awaited_once()
    kwargs = engine.reprotect_position.await_args.kwargs
    assert kwargs["option_symbol"] == "AAPL260418C00150000"
    assert kwargs["ticker"] == "AAPL"
    assert "side" not in kwargs  # long-only model: reprotect hardcodes BUY
    assert kwargs["qty"] == 3
    assert kwargs["entry_price"] == 2.00


@pytest.mark.asyncio
async def test_monitor_short_direction_no_side_and_legs_threaded():
    """SHORT direction = a bought put (Orion is long-only at the broker).
    The monitor must NOT pass a side (reprotect hardcodes BUY) and must
    thread the registry's missing_legs so only absent legs are re-placed."""
    engine = MagicMock()
    engine.risk_manager = RiskManager()
    engine.risk_manager.mark_unprotected("AAPL", "AAPL260418P00150000", "r", missing_legs=["take_profit"])
    engine.reprotect_position = AsyncMock(return_value=True)

    monitor = PositionMonitor(execution_engine=engine)
    monitor.tracked_positions["AAPL260418P00150000"] = _tracked("AAPL260418P00150000", direction="SHORT")

    await monitor._reprotect_unprotected_positions()
    kwargs = engine.reprotect_position.await_args.kwargs
    assert "side" not in kwargs
    assert kwargs["missing_legs"] == ["take_profit"]


@pytest.mark.asyncio
async def test_monitor_skips_unprotected_without_tracked_position():
    """No live tracked position → leave registered, don't guess entry/qty."""
    engine = MagicMock()
    engine.risk_manager = RiskManager()
    engine.risk_manager.mark_unprotected("AAPL", "AAPL260418C00150000", "r")
    engine.reprotect_position = AsyncMock(return_value=True)

    monitor = PositionMonitor(execution_engine=engine)
    # tracked_positions intentionally empty.

    await monitor._reprotect_unprotected_positions()
    engine.reprotect_position.assert_not_awaited()
    assert "AAPL260418C00150000" in engine.risk_manager.get_unprotected()


@pytest.mark.asyncio
async def test_monitor_reprotect_noop_when_no_engine():
    monitor = PositionMonitor(execution_engine=None)
    # Must not raise.
    await monitor._reprotect_unprotected_positions()


@pytest.mark.asyncio
async def test_monitor_reprotect_per_symbol_failure_does_not_block_others():
    engine = MagicMock()
    engine.risk_manager = RiskManager()
    engine.risk_manager.mark_unprotected("AAPL", "AAPL260418C00150000", "r")
    engine.risk_manager.mark_unprotected("MSFT", "MSFT260418C00400000", "r")

    async def _reprotect(**kwargs):
        if kwargs["option_symbol"] == "AAPL260418C00150000":
            raise RuntimeError("boom")
        return True

    engine.reprotect_position = AsyncMock(side_effect=_reprotect)

    monitor = PositionMonitor(execution_engine=engine)
    monitor.tracked_positions["AAPL260418C00150000"] = _tracked("AAPL260418C00150000")
    monitor.tracked_positions["MSFT260418C00400000"] = _tracked("MSFT260418C00400000")

    # The first symbol raising must not stop the second from being attempted.
    await monitor._reprotect_unprotected_positions()
    assert engine.reprotect_position.await_count == 2


# ── Registry never blocks order flow ────────────────────────────────────────


def test_registry_does_not_gate_check_order():
    """A populated unprotected registry must NOT change a pre-trade check.

    The registry is advisory; it has no pre-trade gate. Marking a symbol
    unprotected must leave check_order's decision untouched.
    """
    rm = RiskManager()
    rm.current_equity = 100_000.0

    before = rm.check_order(ticker="AAPL", quantity=1, price=2.00, side="buy")
    rm.mark_unprotected("AAPL", "AAPL260418C00150000", "stop_loss: 403")
    after = rm.check_order(ticker="AAPL", quantity=1, price=2.00, side="buy")

    assert before == after
