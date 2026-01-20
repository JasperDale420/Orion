"""
Integration tests for order rate limiter.

Tests token bucket algorithm and rate limiting behavior.
"""

import asyncio
import pytest
import time

from orion.execution.rate_limiter import (
    OrderRateLimiter,
    RateLimitExceeded,
    get_order_rate_limiter,
    reset_order_rate_limiter,
)


class TestOrderRateLimiter:
    """Tests for the OrderRateLimiter."""

    def test_try_acquire_success(self):
        """Should acquire slot when under limit."""
        limiter = OrderRateLimiter(max_per_minute=10)

        result = limiter.try_acquire()
        assert result is True
        assert limiter.requests_in_window == 1

    def test_try_acquire_at_limit(self):
        """Should reject when at limit."""
        limiter = OrderRateLimiter(max_per_minute=5)

        # Fill up the limit
        for _ in range(5):
            assert limiter.try_acquire() is True

        # Should be rejected
        assert limiter.try_acquire() is False
        assert limiter.requests_in_window == 5

    def test_available_capacity(self):
        """Should report correct available capacity."""
        limiter = OrderRateLimiter(max_per_minute=10)

        assert limiter.available_capacity == 10

        for _ in range(3):
            limiter.try_acquire()

        assert limiter.available_capacity == 7

    def test_reset(self):
        """Should clear all tracked requests."""
        limiter = OrderRateLimiter(max_per_minute=10)

        for _ in range(5):
            limiter.try_acquire()

        assert limiter.requests_in_window == 5

        limiter.reset()
        assert limiter.requests_in_window == 0
        assert limiter.available_capacity == 10


class TestOrderRateLimiterAsync:
    """Async tests for the OrderRateLimiter."""

    @pytest.mark.asyncio
    async def test_acquire_success(self):
        """Should acquire slot asynchronously."""
        limiter = OrderRateLimiter(max_per_minute=10)

        result = await limiter.acquire(timeout=1.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_acquire_timeout(self):
        """Should return False when at limit."""
        limiter = OrderRateLimiter(max_per_minute=3)

        # Fill up
        for _ in range(3):
            await limiter.acquire(timeout=1.0)

        # Should return False when at limit
        result = await limiter.acquire(timeout=0.5)
        assert result is False

    @pytest.mark.asyncio
    async def test_acquire_or_raise(self):
        """Should raise RateLimitExceeded when limit hit."""
        limiter = OrderRateLimiter(max_per_minute=2)

        await limiter.acquire_or_raise(timeout=1.0)
        await limiter.acquire_or_raise(timeout=1.0)

        with pytest.raises(RateLimitExceeded):
            await limiter.acquire_or_raise(timeout=0.1)


class TestGlobalRateLimiter:
    """Tests for the global rate limiter singleton."""

    def test_get_order_rate_limiter(self):
        """Should return same instance."""
        reset_order_rate_limiter()

        limiter1 = get_order_rate_limiter()
        limiter2 = get_order_rate_limiter()

        assert limiter1 is limiter2

    def test_reset_order_rate_limiter(self):
        """Should clear the global limiter state."""
        limiter = get_order_rate_limiter()
        limiter.try_acquire()

        assert limiter.requests_in_window >= 1

        reset_order_rate_limiter()
        # After reset, getting limiter again should have clean state
        limiter = get_order_rate_limiter()
        assert limiter.requests_in_window == 0
