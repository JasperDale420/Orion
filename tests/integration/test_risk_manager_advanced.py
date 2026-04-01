"""
Integration tests for advanced RiskManager features.

Tests portfolio-level Greeks limits, sector exposure, and 0DTE wind-down.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from orion.config import RiskSettings
from orion.execution.risk.manager import RiskManager


class TestGreeksLimits:
    """Tests for portfolio-level Greeks limits."""

    def test_check_greeks_limits_within_limits(self):
        """Should allow trades within Greeks limits."""
        settings = RiskSettings(
            max_portfolio_delta=500.0,
            max_portfolio_gamma=100.0,
            max_position_delta=100.0,
            enable_greeks_checks=True,
        )
        rm = RiskManager(config=settings)

        result = rm._check_greeks_limits(settings, "AAPL", position_delta=50.0)
        assert result is True

    def test_check_greeks_limits_position_delta_breach(self):
        """Should reject when position delta exceeds limit."""
        settings = RiskSettings(
            max_position_delta=100.0,
            enable_greeks_checks=True,
        )
        rm = RiskManager(config=settings)

        result = rm._check_greeks_limits(settings, "AAPL", position_delta=150.0)
        assert result is False

    def test_check_greeks_limits_portfolio_delta_breach(self):
        """Should reject when portfolio delta would exceed limit."""
        settings = RiskSettings(
            max_portfolio_delta=200.0,
            max_position_delta=100.0,
            enable_greeks_checks=True,
        )
        rm = RiskManager(config=settings)
        rm.portfolio_delta = 150.0  # Already have exposure

        result = rm._check_greeks_limits(settings, "AAPL", position_delta=75.0)
        assert result is False  # 150 + 75 = 225 > 200

    def test_greeks_checks_disabled(self):
        """Should allow all trades when Greeks checks disabled."""
        settings = RiskSettings(
            max_position_delta=10.0,  # Very low
            enable_greeks_checks=False,
        )
        rm = RiskManager(config=settings)

        result = rm._check_greeks_limits(settings, "AAPL", position_delta=100.0)
        assert result is True

    def test_update_position_greeks(self):
        """Should update position Greeks and recalculate portfolio totals."""
        rm = RiskManager()

        rm.update_position_greeks("AAPL", delta=50.0, gamma=5.0, theta=-2.0, vega=1.0)
        rm.update_position_greeks("MSFT", delta=30.0, gamma=3.0, theta=-1.5, vega=0.5)

        assert rm.portfolio_delta == 80.0
        assert rm.portfolio_gamma == 8.0
        assert "AAPL" in rm.position_greeks
        assert "MSFT" in rm.position_greeks

    def test_clear_position_greeks(self):
        """Should clear position Greeks and update portfolio totals."""
        rm = RiskManager()
        rm.update_position_greeks("AAPL", delta=50.0, gamma=5.0)
        rm.update_position_greeks("MSFT", delta=30.0, gamma=3.0)

        rm.clear_position_greeks("AAPL")

        assert rm.portfolio_delta == 30.0
        assert rm.portfolio_gamma == 3.0
        assert "AAPL" not in rm.position_greeks


class TestSectorExposure:
    """Tests for sector concentration limits."""

    def test_check_sector_exposure_within_limits(self):
        """Should allow trades within sector limits."""
        settings = RiskSettings(
            max_sector_exposure_pct=0.30,
            enable_sector_checks=True,
        )
        rm = RiskManager(config=settings)
        rm.current_equity = 100000.0

        result = rm.check_sector_exposure("Technology", additional_exposure=20000.0)
        assert result is True  # 20k / 100k = 20% < 30%

    def test_check_sector_exposure_breach(self):
        """Should reject when sector exposure would exceed limit."""
        settings = RiskSettings(
            max_sector_exposure_pct=0.30,
            enable_sector_checks=True,
        )
        rm = RiskManager(config=settings)
        rm.current_equity = 100000.0
        rm.sector_exposures["Technology"] = 25000.0

        result = rm.check_sector_exposure("Technology", additional_exposure=10000.0)
        assert result is False  # 25k + 10k = 35% > 30%

    def test_sector_checks_disabled(self):
        """Should allow all trades when sector checks disabled."""
        settings = RiskSettings(
            max_sector_exposure_pct=0.10,  # Very low
            enable_sector_checks=False,
        )
        rm = RiskManager(config=settings)
        rm.current_equity = 100000.0

        result = rm.check_sector_exposure("Technology", additional_exposure=50000.0)
        assert result is True

    def test_update_sector_exposure(self):
        """Should update sector exposure correctly."""
        rm = RiskManager()

        rm.update_sector_exposure("Technology", 10000.0)
        assert rm.sector_exposures.get("Technology") == 10000.0

        rm.update_sector_exposure("Technology", 5000.0)
        assert rm.sector_exposures.get("Technology") == 15000.0

        rm.update_sector_exposure("Technology", -15000.0)
        assert "Technology" not in rm.sector_exposures  # Removed when zero

    def test_get_sector_exposure_pct(self):
        """Should return correct sector exposure percentage."""
        rm = RiskManager()
        rm.current_equity = 100000.0
        rm.sector_exposures["Technology"] = 20000.0

        assert rm.get_sector_exposure_pct("Technology") == 0.20
        assert rm.get_sector_exposure_pct("Healthcare") == 0.0


class TestZeroDteWinddown:
    """Tests for 0DTE time-of-day wind-down."""

    def test_normal_trading_hours(self):
        """Should allow 0DTE trades during normal hours."""
        settings = RiskSettings(
            zero_dte_cutoff_minutes=60,
            zero_dte_reduce_size_after_minutes=120,
            enable_zero_dte_winddown=True,
        )
        rm = RiskManager(config=settings)

        # 10:00 AM ET - plenty of time
        timestamp = datetime(2025, 1, 15, 10, 0, tzinfo=ZoneInfo("America/New_York"))
        allowed, reason = rm.check_zero_dte_winddown(dte=0, timestamp=timestamp)

        assert allowed is True
        assert reason == "Normal trading"

    def test_reduced_size_window(self):
        """Should allow but flag reduced size in 60-120 min window."""
        settings = RiskSettings(
            zero_dte_cutoff_minutes=60,
            zero_dte_reduce_size_after_minutes=120,
            zero_dte_reduced_size_pct=0.50,
            enable_zero_dte_winddown=True,
        )
        rm = RiskManager(config=settings)

        # 2:30 PM ET - 90 min to close
        timestamp = datetime(2025, 1, 15, 14, 30, tzinfo=ZoneInfo("America/New_York"))
        allowed, reason = rm.check_zero_dte_winddown(dte=0, timestamp=timestamp)

        assert allowed is True
        assert "Reduce size: 50%" in reason

    def test_hard_cutoff(self):
        """Should block 0DTE trades within cutoff window."""
        settings = RiskSettings(
            zero_dte_cutoff_minutes=60,
            enable_zero_dte_winddown=True,
        )
        rm = RiskManager(config=settings)

        # 3:30 PM ET - only 30 min to close
        timestamp = datetime(2025, 1, 15, 15, 30, tzinfo=ZoneInfo("America/New_York"))
        allowed, reason = rm.check_zero_dte_winddown(dte=0, timestamp=timestamp)

        assert allowed is False
        assert "cutoff" in reason.lower()

    def test_winddown_disabled(self):
        """Should allow all 0DTE trades when wind-down disabled."""
        settings = RiskSettings(
            zero_dte_cutoff_minutes=60,
            enable_zero_dte_winddown=False,
        )
        rm = RiskManager(config=settings)

        # 3:30 PM ET - would be blocked if enabled
        timestamp = datetime(2025, 1, 15, 15, 30, tzinfo=ZoneInfo("America/New_York"))
        allowed, reason = rm.check_zero_dte_winddown(dte=0, timestamp=timestamp)

        assert allowed is True
        assert reason == "Wind-down disabled"

    def test_non_zero_dte_not_affected(self):
        """Should not affect non-0DTE trades."""
        settings = RiskSettings(
            zero_dte_cutoff_minutes=60,
            enable_zero_dte_winddown=True,
        )
        rm = RiskManager(config=settings)

        # 3:30 PM ET - would be blocked for 0DTE
        timestamp = datetime(2025, 1, 15, 15, 30, tzinfo=ZoneInfo("America/New_York"))
        allowed, reason = rm.check_zero_dte_winddown(dte=1, timestamp=timestamp)

        assert allowed is True
        assert reason == "Not 0DTE"

    def test_get_size_multiplier_full_size(self):
        """Should return 1.0 during normal hours."""
        settings = RiskSettings(
            zero_dte_cutoff_minutes=60,
            zero_dte_reduce_size_after_minutes=120,
            enable_zero_dte_winddown=True,
        )
        rm = RiskManager(config=settings)

        timestamp = datetime(2025, 1, 15, 10, 0, tzinfo=ZoneInfo("America/New_York"))
        multiplier = rm.get_zero_dte_size_multiplier(dte=0, timestamp=timestamp)

        assert multiplier == 1.0

    def test_get_size_multiplier_reduced(self):
        """Should return reduced multiplier in wind-down window."""
        settings = RiskSettings(
            zero_dte_cutoff_minutes=60,
            zero_dte_reduce_size_after_minutes=120,
            zero_dte_reduced_size_pct=0.50,
            enable_zero_dte_winddown=True,
        )
        rm = RiskManager(config=settings)

        timestamp = datetime(2025, 1, 15, 14, 30, tzinfo=ZoneInfo("America/New_York"))
        multiplier = rm.get_zero_dte_size_multiplier(dte=0, timestamp=timestamp)

        assert multiplier == 0.50

    def test_get_size_multiplier_blocked(self):
        """Should return 0.0 when blocked."""
        settings = RiskSettings(
            zero_dte_cutoff_minutes=60,
            enable_zero_dte_winddown=True,
        )
        rm = RiskManager(config=settings)

        timestamp = datetime(2025, 1, 15, 15, 30, tzinfo=ZoneInfo("America/New_York"))
        multiplier = rm.get_zero_dte_size_multiplier(dte=0, timestamp=timestamp)

        assert multiplier == 0.0
