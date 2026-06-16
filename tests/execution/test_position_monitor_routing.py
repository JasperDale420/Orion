"""Tests for PositionMonitor routing closes through ExecutionEngine.

Verifies that PositionMonitor.execute_exits() routes close orders through
the ExecutionEngine (not direct broker calls) when an engine is configured,
and that it uses the _closing_symbols guard from PositionManager to prevent
duplicate close orders.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.execution.position_manager import PositionManager
from orion.execution.position_monitor import PositionMonitor, TrackedPosition
from orion.ml.exit_classifier import ExitPrediction


def _tracked_position(
    symbol: str = "AAPL",
    qty: float = 10.0,
    entry_price: float = 150.0,
    current_price: float = 155.0,
    bucket: str = "SWING",
    option_symbol: str | None = None,
    decision_id: str | None = "dec-123",
) -> TrackedPosition:
    """Build a TrackedPosition for testing."""
    return TrackedPosition(
        symbol=symbol,
        qty=qty,
        entry_price=entry_price,
        current_price=current_price,
        unrealized_pnl_pct=((current_price - entry_price) / entry_price) * 100,
        entry_time=datetime.now(UTC),
        bucket=bucket,
        decision_id=decision_id,
        option_symbol=option_symbol,
    )


def _exit_prediction(
    should_exit: bool = True,
    confidence: float = 0.85,
    reasoning: str = "test exit signal",
) -> ExitPrediction:
    """Build an ExitPrediction for testing."""
    return ExitPrediction(
        should_exit=should_exit,
        confidence=confidence,
        reasoning=reasoning,
    )


@pytest.mark.unit
class TestPositionMonitorRouting:
    """Tests that PositionMonitor routes exits through ExecutionEngine."""

    @pytest.mark.asyncio
    async def test_routes_close_through_execution_engine(self) -> None:
        """When ExecutionEngine is configured, closes must go through it, not the broker."""
        engine = MagicMock()
        engine.close_position = AsyncMock(return_value=True)

        pm = PositionManager()
        monitor = PositionMonitor(execution_engine=engine, position_manager=pm)

        pos = _tracked_position(symbol="AAPL", qty=10.0, bucket="SWING")
        prediction = _exit_prediction()
        connector = MagicMock()

        with patch("orion.ml.performance_tracker.log_exit_prediction", new_callable=AsyncMock):
            with patch("orion.ml.performance_tracker.log_outcome", new_callable=AsyncMock):
                results = await monitor.execute_exits(connector, [(pos, prediction)], dry_run=False)

        # ExecutionEngine.close_position should have been called
        engine.close_position.assert_called_once()
        call_kwargs = engine.close_position.call_args.kwargs
        assert call_kwargs["ticker"] == "AAPL"
        assert call_kwargs["qty"] == 10.0
        # Phase 2 of exit-pipeline RCA: position_monitor now always
        # passes `use_market_order=False` and lets close_position
        # decide. For equity + IMMEDIATE urgency it still routes
        # through the market path; for options it forces limit
        # (otherwise Alpaca rejects with 42210000 outside RTH).
        assert call_kwargs["use_market_order"] is False
        assert call_kwargs["direction"] == "LONG"
        # current_price must be threaded through so close_position
        # can derive an option-tick-rounded limit without a separate
        # quote lookup.
        assert "current_price" in call_kwargs

        # Connector should NOT have been called directly
        connector.close_position.assert_not_called()

        # Should report success
        assert len(results) == 1
        assert results[0]["executed"] is True

    @pytest.mark.asyncio
    async def test_close_request_includes_proper_exit_signal(self) -> None:
        """The exit_signal passed to ExecutionEngine must include bucket, pnl, and reasoning."""
        engine = MagicMock()
        engine.close_position = AsyncMock(return_value=True)

        pm = PositionManager()
        monitor = PositionMonitor(execution_engine=engine, position_manager=pm)

        pos = _tracked_position(symbol="TSLA", qty=5.0, bucket="0DTE")
        prediction = _exit_prediction(confidence=0.92, reasoning="theta decay")
        connector = MagicMock()

        with patch("orion.ml.performance_tracker.log_exit_prediction", new_callable=AsyncMock):
            with patch("orion.ml.performance_tracker.log_outcome", new_callable=AsyncMock):
                await monitor.execute_exits(connector, [(pos, prediction)], dry_run=False)

        call_kwargs = engine.close_position.call_args.kwargs
        exit_signal = call_kwargs["exit_signal"]

        assert exit_signal.rule_id == "ml_exit_0DTE"
        assert exit_signal.reason == "theta decay"
        assert exit_signal.urgency == "IMMEDIATE"
        assert exit_signal.confidence == 0.92
        assert exit_signal.details["bucket"] == "0DTE"
        assert "pnl_pct" in exit_signal.details

    @pytest.mark.asyncio
    async def test_falls_back_to_connector_when_no_engine(self) -> None:
        """Without an ExecutionEngine, the monitor should fall back to direct connector calls."""
        pm = PositionManager()
        monitor = PositionMonitor(execution_engine=None, position_manager=pm)

        pos = _tracked_position(symbol="MSFT", qty=3.0, option_symbol=None)
        prediction = _exit_prediction()
        connector = MagicMock()
        connector.close_position.return_value = SimpleNamespace(id="order-1")

        with patch("orion.ml.performance_tracker.log_exit_prediction", new_callable=AsyncMock):
            with patch("orion.ml.performance_tracker.log_outcome", new_callable=AsyncMock):
                results = await monitor.execute_exits(connector, [(pos, prediction)], dry_run=False)

        connector.close_position.assert_called_once_with("MSFT")
        assert results[0]["executed"] is True


@pytest.mark.unit
class TestPositionMonitorClosingGuardIntegration:
    """Tests that PositionMonitor uses the _closing_symbols guard from PositionManager."""

    @pytest.mark.asyncio
    async def test_blocks_duplicate_close_via_guard(self) -> None:
        """If a symbol is already marked as closing, the ML exit should be blocked."""
        engine = MagicMock()
        engine.close_position = AsyncMock(return_value=True)

        pm = PositionManager()
        pm.mark_closing("AAPL")  # Simulate a close already in progress

        monitor = PositionMonitor(execution_engine=engine, position_manager=pm)

        pos = _tracked_position(symbol="AAPL")
        prediction = _exit_prediction()
        connector = MagicMock()

        with patch("orion.ml.performance_tracker.log_exit_prediction", new_callable=AsyncMock):
            results = await monitor.execute_exits(connector, [(pos, prediction)], dry_run=False)

        # Should NOT have called the engine
        engine.close_position.assert_not_called()

        assert len(results) == 1
        assert results[0]["error"] == "close_already_in_progress"
        assert results[0]["executed"] is False

    @pytest.mark.asyncio
    async def test_unmarks_closing_after_successful_close(self) -> None:
        """After a successful close, the closing guard must be released."""
        engine = MagicMock()
        engine.close_position = AsyncMock(return_value=True)

        pm = PositionManager()
        monitor = PositionMonitor(execution_engine=engine, position_manager=pm)

        pos = _tracked_position(symbol="AAPL")
        prediction = _exit_prediction()
        connector = MagicMock()

        with patch("orion.ml.performance_tracker.log_exit_prediction", new_callable=AsyncMock):
            with patch("orion.ml.performance_tracker.log_outcome", new_callable=AsyncMock):
                await monitor.execute_exits(connector, [(pos, prediction)], dry_run=False)

        # Guard should be released
        assert not pm.is_closing("AAPL")

    @pytest.mark.asyncio
    async def test_unmarks_closing_after_failed_close(self) -> None:
        """After a failed close, the closing guard must still be released (finally block)."""
        engine = MagicMock()
        engine.close_position = AsyncMock(side_effect=RuntimeError("broker timeout"))

        pm = PositionManager()
        monitor = PositionMonitor(execution_engine=engine, position_manager=pm)

        pos = _tracked_position(symbol="AAPL")
        prediction = _exit_prediction()
        connector = MagicMock()

        with patch("orion.ml.performance_tracker.log_exit_prediction", new_callable=AsyncMock):
            results = await monitor.execute_exits(connector, [(pos, prediction)], dry_run=False)

        # Guard should still be released even on failure
        assert not pm.is_closing("AAPL")

        assert len(results) == 1
        assert results[0]["error"] == "broker timeout"
        assert results[0]["executed"] is False

    @pytest.mark.asyncio
    async def test_dry_run_does_not_mark_closing(self) -> None:
        """Dry-run exits should not interact with the closing guard."""
        engine = MagicMock()
        pm = PositionManager()
        monitor = PositionMonitor(execution_engine=engine, position_manager=pm)

        pos = _tracked_position(symbol="AAPL")
        prediction = _exit_prediction()
        connector = MagicMock()

        with patch("orion.ml.performance_tracker.log_exit_prediction", new_callable=AsyncMock):
            results = await monitor.execute_exits(connector, [(pos, prediction)], dry_run=True)

        assert not pm.is_closing("AAPL")
        engine.close_position.assert_not_called()
        assert results[0]["error"] == "dry_run"
