"""close_position tolerates a transient Gateway blip.

`_check_gateway_available` caches its result for 60s, so a momentary blip's
`False` stuck for a full minute — and close_position bailed immediately on it,
delaying a critical exit until the next position-monitor cycle. The close path
now retries a fresh (cache-bypassing) availability probe a few times before
giving up.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from orion.execution.execution_engine import ExecutionEngine


def _engine():
    ee = ExecutionEngine()
    ee._CLOSE_GATEWAY_RETRY_BACKOFF_SECONDS = 0.0  # no real sleeps in tests
    return ee


@pytest.mark.asyncio
async def test_force_bypasses_gateway_availability_cache():
    ee = _engine()
    client = AsyncMock()
    client.get_clock.return_value = {"is_open": True}  # gateway is actually up
    ee._get_gateway_client = lambda: client
    # Prime a cached "unavailable" from a blip moments ago.
    ee._gateway_available = False
    ee._gateway_check_ts = datetime.now(UTC)

    assert await ee._check_gateway_available() is False  # cache hit, stale False
    assert await ee._check_gateway_available(force=True) is True  # fresh probe


@pytest.mark.asyncio
async def test_close_gateway_retry_succeeds_when_gateway_recovers():
    ee = _engine()
    ee._check_gateway_available = AsyncMock(side_effect=[False, True])
    assert await ee._gateway_available_for_close("AAPL260529P00315000") is True
    assert ee._check_gateway_available.await_count == 2


@pytest.mark.asyncio
async def test_close_gateway_retry_gives_up_after_max_attempts():
    ee = _engine()
    ee._check_gateway_available = AsyncMock(return_value=False)
    assert await ee._gateway_available_for_close("AAPL260529P00315000") is False
    assert ee._check_gateway_available.await_count == ee._CLOSE_GATEWAY_RETRY_ATTEMPTS


@pytest.mark.asyncio
async def test_close_position_bails_after_retries_when_gateway_down():
    ee = _engine()
    ee._check_gateway_available = AsyncMock(return_value=False)
    client = AsyncMock()
    ee._get_gateway_client = lambda: client

    result = await ee.close_position(ticker="AAPL260529P00315000", qty=10, exit_signal=object(), current_price=5.0)

    assert result is False
    # Retried, not a single shot.
    assert ee._check_gateway_available.await_count == ee._CLOSE_GATEWAY_RETRY_ATTEMPTS
    client.create_order.assert_not_called()
    # State effect: bailing before any submit is NOT a broker round-trip, so
    # nothing lands in the breaker history. A regression that recorded a False
    # here would let a flaky gateway erode the circuit breaker on its own.
    assert list(ee.order_history) == []
    # And the native flatten was never reached either.
    client.close_position.assert_not_called()
