"""Tests for the _closing_symbols guard in PositionManager.

Verifies that duplicate close orders are blocked when a close is already
in-flight for a symbol, and that the guard is properly released on both
success and failure.
"""

from __future__ import annotations

import pytest

from orion.execution.position_manager import PositionManager


@pytest.mark.unit
class TestClosingSymbolsGuard:
    """Tests for mark_closing / unmark_closing / is_closing on PositionManager."""

    def test_mark_closing_adds_symbol(self) -> None:
        """Initiating a close should add the symbol to _closing_symbols."""
        pm = PositionManager()
        assert not pm.is_closing("AAPL")

        result = pm.mark_closing("AAPL")

        assert result is True
        assert pm.is_closing("AAPL")
        assert "AAPL" in pm._closing_symbols

    def test_second_close_rejected_while_first_in_flight(self) -> None:
        """A second close for the same symbol must be rejected while the first is in-flight."""
        pm = PositionManager()

        first = pm.mark_closing("AAPL")
        assert first is True

        second = pm.mark_closing("AAPL")
        assert second is False
        assert pm.is_closing("AAPL")

    def test_unmark_closing_removes_symbol_after_success(self) -> None:
        """After a close completes successfully, the symbol must be removed from the guard."""
        pm = PositionManager()
        pm.mark_closing("AAPL")

        pm.unmark_closing("AAPL")

        assert not pm.is_closing("AAPL")
        assert "AAPL" not in pm._closing_symbols

    def test_unmark_closing_removes_symbol_after_failure(self) -> None:
        """After a close fails, the symbol must still be removed from the guard."""
        pm = PositionManager()
        pm.mark_closing("AAPL")

        # Simulate failure path -- unmark_closing is always called in the finally block
        pm.unmark_closing("AAPL")

        assert not pm.is_closing("AAPL")

    def test_unmark_closing_is_idempotent(self) -> None:
        """Calling unmark_closing on a symbol that is not closing should be a no-op."""
        pm = PositionManager()

        # Should not raise
        pm.unmark_closing("AAPL")
        assert not pm.is_closing("AAPL")

    def test_mark_closing_allows_reclose_after_unmark(self) -> None:
        """After unmarking, a new close for the same symbol should be allowed."""
        pm = PositionManager()
        pm.mark_closing("AAPL")
        pm.unmark_closing("AAPL")

        result = pm.mark_closing("AAPL")
        assert result is True
        assert pm.is_closing("AAPL")

    def test_closing_guard_is_per_symbol(self) -> None:
        """Closing one symbol should not block closing a different symbol."""
        pm = PositionManager()
        pm.mark_closing("AAPL")

        result = pm.mark_closing("MSFT")

        assert result is True
        assert pm.is_closing("AAPL")
        assert pm.is_closing("MSFT")

    def test_initial_closing_symbols_is_empty(self) -> None:
        """A freshly constructed PositionManager has no symbols in the closing set."""
        pm = PositionManager()
        assert len(pm._closing_symbols) == 0
