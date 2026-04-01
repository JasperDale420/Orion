"""Unit tests for projected Greeks and vega limits.

Tests the fixes for:
1. Projected gamma calculation (was using current, now uses projected)
2. Vega exposure limits (new feature)
"""

from orion.config import RiskSettings
from orion.execution.risk.manager import RiskManager


class TestProjectedGamma:
    """Tests for projected gamma calculation fix."""

    def test_projected_gamma_blocks_breach(self):
        """Should reject when projected gamma would exceed limit."""
        settings = RiskSettings(
            max_portfolio_gamma=100.0,
            enable_greeks_checks=True,
        )
        rm = RiskManager(config=settings)
        rm.portfolio_gamma = 80.0  # Already have exposure

        # This trade would push gamma to 130, exceeding 100 limit
        result = rm._check_greeks_limits(settings, "AAPL", position_delta=50.0, position_gamma=50.0)
        assert result is False

    def test_projected_gamma_allows_within_limit(self):
        """Should allow when projected gamma is within limit."""
        settings = RiskSettings(
            max_portfolio_gamma=100.0,
            enable_greeks_checks=True,
        )
        rm = RiskManager(config=settings)
        rm.portfolio_gamma = 40.0

        # This trade would push gamma to 60, within 100 limit
        result = rm._check_greeks_limits(settings, "AAPL", position_delta=50.0, position_gamma=20.0)
        assert result is True

    def test_gamma_at_exact_limit_allowed(self):
        """Should allow when projected gamma exactly equals limit."""
        settings = RiskSettings(
            max_portfolio_gamma=100.0,
            enable_greeks_checks=True,
        )
        rm = RiskManager(config=settings)
        rm.portfolio_gamma = 50.0

        # This trade would push gamma to exactly 100
        result = rm._check_greeks_limits(settings, "AAPL", position_delta=50.0, position_gamma=50.0)
        assert result is True

    def test_gamma_just_over_limit_blocked(self):
        """Should reject when projected gamma is just over limit."""
        settings = RiskSettings(
            max_portfolio_gamma=100.0,
            enable_greeks_checks=True,
        )
        rm = RiskManager(config=settings)
        rm.portfolio_gamma = 50.0

        # This trade would push gamma to 100.1
        result = rm._check_greeks_limits(settings, "AAPL", position_delta=50.0, position_gamma=50.1)
        assert result is False


class TestVegaLimits:
    """Tests for vega exposure limits."""

    def test_vega_limit_blocks_portfolio_breach(self):
        """Should reject when portfolio vega exceeds limit."""
        settings = RiskSettings(
            max_portfolio_vega=200.0,
            max_position_vega=100.0,
            enable_greeks_checks=True,
        )
        rm = RiskManager(config=settings)
        rm.portfolio_vega = 180.0

        # This trade would push vega to 230, exceeding 200 limit
        result = rm._check_greeks_limits(settings, "AAPL", position_delta=50.0, position_gamma=10.0, position_vega=50.0)
        assert result is False

    def test_position_vega_limit(self):
        """Should reject when single position vega exceeds limit."""
        settings = RiskSettings(
            max_position_vega=50.0,
            enable_greeks_checks=True,
        )
        rm = RiskManager(config=settings)

        result = rm._check_greeks_limits(settings, "AAPL", position_delta=50.0, position_gamma=10.0, position_vega=75.0)
        assert result is False

    def test_vega_within_limits_allowed(self):
        """Should allow when vega is within all limits."""
        settings = RiskSettings(
            max_portfolio_vega=200.0,
            max_position_vega=50.0,
            enable_greeks_checks=True,
        )
        rm = RiskManager(config=settings)
        rm.portfolio_vega = 100.0

        result = rm._check_greeks_limits(settings, "AAPL", position_delta=50.0, position_gamma=10.0, position_vega=40.0)
        assert result is True

    def test_portfolio_vega_tracking(self):
        """Should correctly track portfolio vega across positions."""
        rm = RiskManager()

        rm.update_position_greeks("AAPL", delta=50.0, gamma=5.0, vega=30.0)
        rm.update_position_greeks("MSFT", delta=30.0, gamma=3.0, vega=20.0)

        assert rm.portfolio_vega == 50.0
        assert rm.portfolio_delta == 80.0
        assert rm.portfolio_gamma == 8.0

    def test_clear_position_updates_vega(self):
        """Should update portfolio vega when position is cleared."""
        rm = RiskManager()
        rm.update_position_greeks("AAPL", delta=50.0, gamma=5.0, vega=30.0)
        rm.update_position_greeks("MSFT", delta=30.0, gamma=3.0, vega=20.0)

        rm.clear_position_greeks("AAPL")

        assert rm.portfolio_vega == 20.0
        assert rm.portfolio_delta == 30.0
        assert rm.portfolio_gamma == 3.0


class TestCheckOptionsOrderWithVega:
    """Tests for check_options_order with all Greeks."""

    def test_check_options_order_passes_all_greeks(self):
        """Should pass all Greeks to _check_greeks_limits."""
        settings = RiskSettings(
            max_portfolio_delta=500.0,
            max_portfolio_gamma=100.0,
            max_portfolio_vega=200.0,
            max_position_delta=100.0,
            max_position_vega=50.0,
            enable_greeks_checks=True,
        )
        rm = RiskManager(config=settings)
        rm.current_equity = 100000.0

        result = rm.check_options_order(
            ticker="AAPL",
            quantity=1,
            price=2.50,
            side="buy",
            delta=50.0,
            gamma=10.0,
            vega=30.0,
        )
        assert result is True

    def test_check_options_order_rejects_vega_breach(self):
        """Should reject options order when vega exceeds limit."""
        settings = RiskSettings(
            max_position_vega=50.0,
            enable_greeks_checks=True,
        )
        rm = RiskManager(config=settings)
        rm.current_equity = 100000.0

        result = rm.check_options_order(
            ticker="AAPL",
            quantity=1,
            price=2.50,
            side="buy",
            delta=50.0,
            gamma=10.0,
            vega=75.0,  # Exceeds limit
        )
        assert result is False


class TestGreeksChecksDisabled:
    """Tests for when Greeks checks are disabled."""

    def test_all_greeks_allowed_when_disabled(self):
        """Should allow all positions when Greeks checks disabled."""
        settings = RiskSettings(
            max_portfolio_delta=10.0,
            max_portfolio_gamma=5.0,
            max_portfolio_vega=10.0,
            max_position_delta=5.0,
            max_position_vega=5.0,
            enable_greeks_checks=False,
        )
        rm = RiskManager(config=settings)

        # All values exceed limits but checks are disabled
        result = rm._check_greeks_limits(
            settings, "AAPL", position_delta=100.0, position_gamma=100.0, position_vega=100.0
        )
        assert result is True
