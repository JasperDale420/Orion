"""The global circuit breaker must mean what its API says.

`ORION_GLOBAL_CIRCUIT_BREAKER_ENABLED=false` — set by the native execution
wrapper and by docker-compose — made `CircuitBreaker.is_open()` return False
while the `GLOBAL_CIRCUIT_BREAKER` row said OPEN. It disabled nothing:
`ExecutionEngine._check_system_health` reads the same row directly and blocked
every entry regardless. All the flag achieved was an untrue `is_open()` and one
`global_circuit_breaker_would_open_but_disabled` WARNING per call — 2,287 of
them on 2026-08-14 alone.

Pinned here:
  * an OPEN row means `is_open()` is True, with no env flag able to change that;
  * reading an open breaker is silent — only state CHANGES log.
"""

from unittest.mock import MagicMock

import pytest

from orion.core.circuit_breaker import CircuitBreaker
from orion.storage.db import init_db

pytestmark = pytest.mark.asyncio


async def test_no_env_flag_can_disable_the_breaker(monkeypatch) -> None:
    """The disable flag is gone: an OPEN row blocks trading, full stop."""
    from orion.config import SystemSettings

    monkeypatch.setenv("ORION_GLOBAL_CIRCUIT_BREAKER_ENABLED", "false")
    await init_db()

    breaker = CircuitBreaker()
    await breaker.open("Drawdown kill switch")

    assert await breaker.is_open() is True
    assert not hasattr(SystemSettings(), "global_circuit_breaker_enabled")


async def test_reading_an_open_breaker_is_silent(monkeypatch) -> None:
    """No per-call log line. A read is a read; the log records state changes."""
    await init_db()
    breaker = CircuitBreaker()
    await breaker.open("Drawdown kill switch")

    fake_logger = MagicMock()
    monkeypatch.setattr("orion.core.circuit_breaker.logger", fake_logger)

    for _ in range(5):
        assert await breaker.is_open() is True

    fake_logger.warning.assert_not_called()
    fake_logger.critical.assert_not_called()


async def test_open_logs_critical_only_on_state_change(monkeypatch) -> None:
    """Re-opening an already-open breaker is a no-op, so it must not re-log.

    `_evaluate_drawdown_kill_switch` calls `open()` on every fill while the
    drawdown persists; logging CRITICAL each time buries the one transition
    that mattered.
    """
    await init_db()
    breaker = CircuitBreaker()
    await breaker.close()

    fake_logger = MagicMock()
    monkeypatch.setattr("orion.core.circuit_breaker.logger", fake_logger)

    await breaker.open("Drawdown kill switch")
    await breaker.open("Drawdown kill switch")
    await breaker.open("Drawdown kill switch")

    assert fake_logger.critical.call_count == 1

    # A genuine second transition logs again.
    await breaker.close()
    await breaker.open("Manual halt")
    assert fake_logger.critical.call_count == 2


async def test_operator_can_open_and_close_the_breaker() -> None:
    """The human path — the admin API calls exactly these two methods."""
    await init_db()
    breaker = CircuitBreaker()

    await breaker.open("Manual halt - investigating issue X")
    assert await breaker.is_open() is True
    assert (await breaker.get_state())["reason"] == "Manual halt - investigating issue X"

    await breaker.close()
    assert await breaker.is_open() is False
