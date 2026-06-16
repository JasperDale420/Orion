"""Tests for OrderRateLimiter.

H-10 from predict 260430-1751-execution-reliability flagged that the original
acquire() held the asyncio.Lock across `await asyncio.sleep`, serializing N
contenders so each waited the full wait_time of the previous holder. The fix
holds the lock only while inspecting / mutating the in-window deque and
releases it before sleeping. These tests pin both the correctness of the
basic protocol and the lock-released-before-sleep contract.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from orion.execution.rate_limiter import (
    OrderRateLimiter,
    RateLimitExceededError,
    get_order_rate_limiter,
    reset_order_rate_limiter,
)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_single_acquire_succeeds_when_bucket_empty() -> None:
    limiter = OrderRateLimiter(max_per_minute=5, window_seconds=1.0)
    assert await limiter.acquire(timeout=0.1) is True
    assert limiter.requests_in_window == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_burst_within_limit_all_succeed() -> None:
    limiter = OrderRateLimiter(max_per_minute=3, window_seconds=1.0)
    results = await asyncio.gather(
        limiter.acquire(timeout=0.1),
        limiter.acquire(timeout=0.1),
        limiter.acquire(timeout=0.1),
    )
    assert all(results) is True
    assert limiter.requests_in_window == 3
    assert limiter.available_capacity == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_returns_false_on_timeout() -> None:
    limiter = OrderRateLimiter(max_per_minute=1, window_seconds=1.0)
    # Fill the bucket
    assert await limiter.acquire(timeout=0.05) is True
    # Next acquire would have to wait ~1s; give it 0.05s and expect False.
    assert await limiter.acquire(timeout=0.05) is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_succeeds_after_window_expires() -> None:
    limiter = OrderRateLimiter(max_per_minute=1, window_seconds=0.1)
    assert await limiter.acquire(timeout=0.05) is True
    # Bucket full — wait long enough for window to expire and slot to be reclaimed.
    assert await limiter.acquire(timeout=0.5) is True
    assert limiter.requests_in_window == 1  # cleanup ran on the second acquire


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_or_raise_raises_on_timeout() -> None:
    limiter = OrderRateLimiter(max_per_minute=1, window_seconds=1.0)
    await limiter.acquire(timeout=0.05)
    with pytest.raises(RateLimitExceededError):
        await limiter.acquire_or_raise(timeout=0.05)


@pytest.mark.unit
def test_try_acquire_succeeds_when_bucket_has_room() -> None:
    limiter = OrderRateLimiter(max_per_minute=2, window_seconds=1.0)
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is True
    # Third would need to wait — try_acquire is non-blocking and returns False.
    assert limiter.try_acquire() is False


@pytest.mark.unit
def test_reset_clears_window() -> None:
    limiter = OrderRateLimiter(max_per_minute=1, window_seconds=1.0)
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is False
    limiter.reset()
    assert limiter.requests_in_window == 0
    assert limiter.try_acquire() is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lock_released_during_sleep() -> None:
    """The acquire's wait must not hold the lock — otherwise N contenders
    serialize on the lock, turning a max(wait) wall-clock into N×wait. We
    verify by directly attempting to take the limiter's internal lock during
    a contender's sleep window. With the original buggy implementation this
    would block until the contender's sleep finished; with the fix the lock
    is acquirable mid-sleep.
    """
    limiter = OrderRateLimiter(max_per_minute=1, window_seconds=0.2)
    # Fill bucket
    assert await limiter.acquire(timeout=0.05) is True

    contender_started = asyncio.Event()
    contender_done = asyncio.Event()

    async def contender() -> None:
        contender_started.set()
        await limiter.acquire(timeout=1.0)
        contender_done.set()

    contender_task = asyncio.create_task(contender())
    await contender_started.wait()
    # Give the contender time to enter its first while-iteration and start
    # sleeping. The fix releases the lock before sleeping; we should be able
    # to take it within a few ms even though the contender is "in" acquire().
    await asyncio.sleep(0.02)

    lock_acquired_during_contender_sleep = False
    try:
        # Wait at most 50ms — well under the contender's ~200ms sleep window.
        async with asyncio.timeout(0.05):
            async with limiter._lock:
                lock_acquired_during_contender_sleep = True
    except TimeoutError:
        pass

    # Wait for contender to finish so we don't leak a task
    await contender_task
    assert contender_done.is_set()

    assert lock_acquired_during_contender_sleep is True, (
        "acquire() held the asyncio.Lock during sleep — would serialize N contenders"
    )


@pytest.mark.unit
def test_singleton_get_returns_same_instance() -> None:
    reset_order_rate_limiter()
    a = get_order_rate_limiter()
    b = get_order_rate_limiter()
    assert a is b


@pytest.mark.unit
def test_available_capacity_tracks_window() -> None:
    limiter = OrderRateLimiter(max_per_minute=5, window_seconds=1.0)
    assert limiter.available_capacity == 5
    assert limiter.try_acquire() is True
    assert limiter.available_capacity == 4
