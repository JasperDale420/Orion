"""
Order Rate Limiter for Alpaca API.

Implements token bucket algorithm to respect Alpaca's 200 requests/minute limit.
"""

import asyncio
import time
from collections import deque

from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger(__name__)


class OrderRateLimiter:
    """
    Token bucket rate limiter for order submissions.

    Alpaca API limits:
    - 200 orders per minute for trading API
    - Burst allowed, but sustained rate must stay under limit

    Usage:
        limiter = OrderRateLimiter(max_per_minute=200)
        if await limiter.acquire():
            # Submit order
        else:
            # Rate limited, wait or reject
    """

    def __init__(
        self,
        max_per_minute: int = 200,
        burst_size: int | None = None,
        window_seconds: float = 60.0,
    ):
        """
        Initialize rate limiter.

        Args:
            max_per_minute: Maximum requests allowed per minute
            burst_size: Max burst allowed (defaults to max_per_minute / 4)
            window_seconds: Time window for rate calculation
        """
        self.max_per_minute = max_per_minute
        self.burst_size = burst_size or max(10, max_per_minute // 4)
        self.window_seconds = window_seconds

        # Track request timestamps
        self._request_times: deque[float] = deque()
        self._lock = asyncio.Lock()

    @property
    def requests_in_window(self) -> int:
        """Count requests within the current time window."""
        self._cleanup_old_requests()
        return len(self._request_times)

    @property
    def available_capacity(self) -> int:
        """Remaining capacity before rate limit hit."""
        return max(0, self.max_per_minute - self.requests_in_window)

    def _cleanup_old_requests(self) -> None:
        """Remove request timestamps outside the window."""
        cutoff = time.monotonic() - self.window_seconds
        while self._request_times and self._request_times[0] < cutoff:
            self._request_times.popleft()

    async def acquire(self, timeout: float = 5.0) -> bool:
        """
        Attempt to acquire a rate limit slot.

        Args:
            timeout: Max seconds to wait for a slot

        Returns:
            True if slot acquired, False if timed out

        The lock is held only while inspecting / mutating the in-window deque
        and is released before sleeping. Holding the lock across `await
        asyncio.sleep` would serialize N contending callers — each would wait
        the full wait_time of the previous holder, turning a 100ms slot wait
        into N×100ms wall-clock under contention. Releasing the lock between
        wait windows lets all contenders share the same sleep period and
        race for the next available slot.
        """
        start_time = time.monotonic()

        while True:
            async with self._lock:
                self._cleanup_old_requests()

                if len(self._request_times) < self.max_per_minute:
                    # Slot available — claim it atomically under the lock.
                    self._request_times.append(time.monotonic())
                    return True

                # Compute how long until the oldest request expires. This must
                # also happen under the lock so the deque isn't mutated mid-read.
                oldest = self._request_times[0]
                wait_time = (oldest + self.window_seconds) - time.monotonic()
                requests_in_window = len(self._request_times)

            # Lock released — sleep without serializing other contenders.
            elapsed = time.monotonic() - start_time

            if wait_time <= 0:
                # Window expired between lock-release and now; retry immediately.
                continue

            if elapsed + wait_time > timeout:
                logger.warning(f"Rate limit timeout: {requests_in_window}/{self.max_per_minute} requests in window")
                return False

            await asyncio.sleep(min(wait_time, timeout - elapsed))

    async def acquire_or_raise(self, timeout: float = 5.0) -> None:
        """Acquire a slot or raise RateLimitExceededError."""
        if not await self.acquire(timeout):
            raise RateLimitExceededError(
                f"Rate limit exceeded: {self.requests_in_window}/{self.max_per_minute} requests/min"
            )

    def try_acquire(self) -> bool:
        """
        Non-blocking attempt to acquire a slot.

        Returns:
            True if slot acquired immediately, False if would need to wait
        """
        self._cleanup_old_requests()

        if len(self._request_times) < self.max_per_minute:
            self._request_times.append(time.monotonic())
            return True

        return False

    def reset(self) -> None:
        """Clear all tracked requests."""
        self._request_times.clear()


class RateLimitExceededError(Exception):
    """Raised when rate limit is exceeded and cannot be waited."""

    pass


# Global rate limiter instance for Alpaca orders
_order_limiter: OrderRateLimiter | None = None


def get_order_rate_limiter() -> OrderRateLimiter:
    """Get or create the global order rate limiter."""
    global _order_limiter
    if _order_limiter is None:
        _order_limiter = OrderRateLimiter(max_per_minute=200)
    return _order_limiter


def reset_order_rate_limiter() -> None:
    """Reset the global rate limiter (for testing)."""
    global _order_limiter
    if _order_limiter:
        _order_limiter.reset()
