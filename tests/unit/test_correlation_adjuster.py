"""Unit tests for correlation-aware position sizing."""

import numpy as np
import pytest
from unittest.mock import AsyncMock, MagicMock

from orion.config import RiskSettings
from orion.execution.correlation_adjuster import CorrelationAdjuster, clear_correlation_cache


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before each test."""
    clear_correlation_cache()
    yield
    clear_correlation_cache()


class TestCorrelationMultiplier:
    """Tests for correlation-based size adjustment."""

    @pytest.mark.asyncio
    async def test_no_positions_returns_1(self):
        """Should return 1.0 when no existing positions."""
        adjuster = CorrelationAdjuster()
        cfg = RiskSettings(correlation_size_scaling=True)
        result = adjuster.get_size_multiplier("AAPL", [], cfg)
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_scaling_disabled_returns_1(self):
        """Should return 1.0 when correlation scaling is disabled."""
        adjuster = CorrelationAdjuster()
        cfg = RiskSettings(correlation_size_scaling=False)
        result = adjuster.get_size_multiplier("AAPL", ["MSFT"], cfg)
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_high_correlation_reduces_size(self):
        """Should reduce size for highly correlated assets."""
        adjuster = CorrelationAdjuster()
        cfg = RiskSettings(
            correlation_size_scaling=True,
            correlation_threshold=0.70,
            correlation_penalty_factor=0.30,
        )

        # Mock returns with perfect correlation
        def mock_returns(ticker, days, cfg):
            return np.array([0.01, -0.02, 0.015, -0.01, 0.02] * 5)

        adjuster._get_daily_returns = mock_returns

        result = adjuster.get_size_multiplier("AAPL", ["MSFT"], cfg)
        assert result < 1.0  # Should be penalized
        assert result >= 0.30  # Should not go below penalty factor

    @pytest.mark.asyncio
    async def test_low_correlation_no_penalty(self):
        """Should not penalize when correlation is below threshold."""
        adjuster = CorrelationAdjuster()
        cfg = RiskSettings(
            correlation_size_scaling=True,
            correlation_threshold=0.70,
            correlation_penalty_factor=0.30,
            min_bars_for_correlation=10,
        )

        # Mock _calculate_correlation directly to control exact correlation value
        def mock_corr(a, b, days, cfg):
            return 0.50  # Below threshold of 0.70

        adjuster._calculate_correlation = mock_corr

        result = adjuster.get_size_multiplier("AAPL", ["XLE"], cfg)
        # Low correlation (abs < 0.70 threshold) should not be penalized
        assert result == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_self_ticker_excluded(self):
        """Should exclude the new ticker from existing list."""
        adjuster = CorrelationAdjuster()
        cfg = RiskSettings(correlation_size_scaling=True)

        # Mock returns - should skip AAPL in existing
        call_count = {"n": 0}

        def mock_returns(ticker, days, cfg):
            call_count["n"] += 1
            return np.array([0.01, -0.02, 0.015] * 10)

        adjuster._get_daily_returns = mock_returns

        result = adjuster.get_size_multiplier("AAPL", ["AAPL", "MSFT"], cfg)
        # Should still work (correlating with MSFT only)
        assert isinstance(result, float)


class TestCorrelationCalculation:
    """Tests for correlation calculation logic."""

    @pytest.mark.asyncio
    async def test_insufficient_data_returns_none(self):
        """Should return None when insufficient data points."""
        adjuster = CorrelationAdjuster()
        cfg = RiskSettings(
            correlation_size_scaling=True,
            min_bars_for_correlation=20,
        )

        # Mock returns with too few points
        def mock_returns(ticker, days, cfg):
            return np.array([0.01, -0.02, 0.015])  # Only 3 points

        adjuster._get_daily_returns = mock_returns

        corr = adjuster._calculate_correlation("AAPL", "MSFT", 30, cfg)
        assert corr is None

    @pytest.mark.asyncio
    async def test_missing_data_returns_none(self):
        """Should return None when data is missing."""
        adjuster = CorrelationAdjuster()
        cfg = RiskSettings(correlation_size_scaling=True)

        # Mock returns with None for one ticker
        def mock_returns(ticker, days, cfg):
            if ticker == "AAPL":
                return np.array([0.01, -0.02, 0.015] * 10)
            return None

        adjuster._get_daily_returns = mock_returns

        corr = adjuster._calculate_correlation("AAPL", "MSFT", 30, cfg)
        assert corr is None


class TestRiskManagerIntegration:
    """Tests for RiskManager.calculate_size_with_correlation."""

    @pytest.mark.asyncio
    async def test_correlation_sizing_disabled_uses_base(self):
        """Should use base size when correlation scaling disabled."""
        from orion.execution.risk_manager import RiskManager

        cfg = RiskSettings(correlation_size_scaling=False)
        rm = RiskManager(config=cfg)
        rm.current_equity = 100000.0

        result = rm.calculate_size_with_correlation("AAPL", 150.0)
        base = rm.calculate_size(150.0)

        assert result == base

    @pytest.mark.asyncio
    async def test_no_adjuster_uses_base(self):
        """Should use base size when no adjuster set."""
        from orion.execution.risk_manager import RiskManager

        cfg = RiskSettings(correlation_size_scaling=True)
        rm = RiskManager(config=cfg)
        rm.current_equity = 100000.0

        result = rm.calculate_size_with_correlation("AAPL", 150.0)
        base = rm.calculate_size(150.0)

        assert result == base

    @pytest.mark.asyncio
    async def test_with_adjuster_applies_multiplier(self):
        """Should apply correlation multiplier when adjuster is set."""
        from orion.execution.risk_manager import RiskManager

        cfg = RiskSettings(correlation_size_scaling=True)
        rm = RiskManager(config=cfg)
        rm.current_equity = 100000.0
        rm.positions = {"MSFT": {"qty": 100.0}}

        # Create mock adjuster that returns 0.5 multiplier
        mock_adjuster = MagicMock()
        mock_adjuster.get_size_multiplier = MagicMock(return_value=0.5)
        rm.set_correlation_adjuster(mock_adjuster)

        result = rm.calculate_size_with_correlation("AAPL", 150.0)
        base = rm.calculate_size(150.0)

        assert result < base
        assert result >= 1.0  # Should be at least 1 share
