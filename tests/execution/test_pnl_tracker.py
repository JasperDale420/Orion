"""
Tests for the P&L Tracker.

Tests position tracking, portfolio summaries, and risk alerts.
"""

import pytest

from orion.config import RiskSettings
from orion.core.pnl_tracker import (
    PnLTracker,
    get_pnl_tracker,
    reset_pnl_tracker,
)


class TestPnLTracker:
    """Tests for the PnLTracker class."""

    def test_update_position_long(self):
        """Should calculate correct P&L for long position."""
        tracker = PnLTracker()

        position = tracker.update_position(
            ticker="AAPL",
            quantity=100,
            avg_entry_price=150.0,
            current_price=155.0,
            side="long",
        )

        assert position.ticker == "AAPL"
        assert position.market_value == 15500.0
        assert position.unrealized_pnl == 500.0  # (155 - 150) * 100
        assert position.unrealized_pnl_pct == pytest.approx(0.0333, rel=0.01)

    def test_update_position_short(self):
        """Should calculate correct P&L for short position."""
        tracker = PnLTracker()

        position = tracker.update_position(
            ticker="TSLA",
            quantity=-50,
            avg_entry_price=200.0,
            current_price=190.0,
            side="short",
        )

        assert position.unrealized_pnl == 500.0  # Profit from price drop
        assert position.side == "short"

    def test_close_position(self):
        """Should record realized P&L when closing position."""
        tracker = PnLTracker()

        tracker.update_position(
            ticker="MSFT",
            quantity=50,
            avg_entry_price=300.0,
            current_price=310.0,
            side="long",
        )

        realized = tracker.close_position("MSFT", exit_price=320.0)

        assert realized == 1000.0  # (320 - 300) * 50
        assert tracker.daily_realized_pnl == 1000.0
        assert tracker.trades_today == 1
        assert tracker.winners == 1
        assert "MSFT" not in tracker.positions

    def test_get_portfolio_summary(self):
        """Should return correct portfolio summary."""
        tracker = PnLTracker()
        tracker.set_starting_equity(100000.0)

        tracker.update_position("AAPL", 100, 150.0, 155.0, side="long")
        tracker.update_position("MSFT", 50, 300.0, 290.0, side="long")

        summary = tracker.get_portfolio_summary()

        assert summary["positions"] == 2
        assert summary["starting_equity"] == 100000.0
        # AAPL: +500, MSFT: -500 = 0 unrealized
        assert abs(summary["unrealized_pnl"]) < 1  # Near zero
        assert "timestamp" in summary

    def test_get_sector_breakdown(self):
        """Should group positions by sector."""
        tracker = PnLTracker()

        tracker.update_position("AAPL", 100, 150.0, 155.0, side="long", sector="Technology")
        tracker.update_position("MSFT", 50, 300.0, 310.0, side="long", sector="Technology")
        tracker.update_position("JNJ", 30, 160.0, 165.0, side="long", sector="Healthcare")

        sectors = tracker.get_sector_breakdown()

        assert "Technology" in sectors
        assert "Healthcare" in sectors
        assert sectors["Technology"]["position_count"] == 2
        assert sectors["Healthcare"]["position_count"] == 1

    def test_check_risk_alerts_daily_loss(self):
        """Should generate alert when daily loss exceeds limit."""
        settings = RiskSettings(max_daily_loss=500.0)
        tracker = PnLTracker(config=settings)
        tracker.set_starting_equity(100000.0)

        # Create a losing position
        tracker.update_position("BAD", 100, 100.0, 94.0, side="long")  # -600 unrealized

        alerts = tracker.check_risk_alerts()

        assert len(alerts) >= 1
        daily_loss_alerts = [a for a in alerts if a.alert_type == "DAILY_LOSS"]
        assert len(daily_loss_alerts) == 1
        assert daily_loss_alerts[0].severity == "CRITICAL"

    def test_check_risk_alerts_drawdown(self):
        """Should generate alert when drawdown exceeds limit."""
        settings = RiskSettings(max_drawdown_pct=0.05)
        tracker = PnLTracker(config=settings)
        tracker.set_starting_equity(100000.0)

        # Create a losing position that causes >5% drawdown
        # Entry: 100, Current: 94 = -6% loss on the position
        # Position value: 100 shares * 100 = $10000
        # Loss: 100 * 6 = $600 unrealized = 0.6% portfolio but need bigger
        tracker.update_position("BAD", 1000, 100.0, 94.0, side="long")  # -$6000 = 6% drawdown

        # Force portfolio summary update to calculate drawdown
        tracker.get_portfolio_summary()

        # Now check alerts - drawdown should be ~6%
        alerts = tracker.check_risk_alerts()

        drawdown_alerts = [a for a in alerts if a.alert_type == "DRAWDOWN"]
        assert len(drawdown_alerts) == 1
        assert drawdown_alerts[0].severity == "CRITICAL"

    def test_reset_daily(self):
        """Should reset daily counters."""
        tracker = PnLTracker()
        tracker.daily_realized_pnl = 500.0
        tracker.trades_today = 5
        tracker.winners = 3
        tracker.losers = 2

        tracker.reset_daily()

        assert tracker.daily_realized_pnl == 0.0
        assert tracker.trades_today == 0
        assert tracker.winners == 0
        assert tracker.losers == 0


class TestGlobalPnLTracker:
    """Tests for the global tracker singleton."""

    def test_get_pnl_tracker_singleton(self):
        """Should return same instance."""
        reset_pnl_tracker()

        tracker1 = get_pnl_tracker()
        tracker2 = get_pnl_tracker()

        assert tracker1 is tracker2

    def test_reset_pnl_tracker(self):
        """Should create new instance after reset."""
        tracker1 = get_pnl_tracker()
        tracker1.trades_today = 10

        reset_pnl_tracker()
        tracker2 = get_pnl_tracker()

        assert tracker2.trades_today == 0
        assert tracker1 is not tracker2
