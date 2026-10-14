"""A slow ingestion startup must not trip the global circuit breaker.

Incident 2026-08-13/14. Services restarted 22:02 UTC, after hours. `HealthMonitor`
stamps `last_heartbeat_ts` at CONSTRUCTION (`orion/core/health_monitor.py`), but
`IngestionService.initialize()` then spends ~425s hydrating the universe and the
feature-engine history without publishing anything. The first `check_health()`
reached is NOT the one in `run()`'s tail (that one is preceded by
`update_heartbeat()`) — it is the one inside `_maybe_run_eod()`, reached from
`_run_cycle() -> _check_overnight_sleep() -> _maybe_run_eod()` on the
market-closed path BEFORE any heartbeat is published. It measured the whole
startup window as a liveness gap:

    GLOBAL_CIRCUIT_BREAKER | OPEN | CRITICAL: Heartbeat missing for 425.76s > 60.0s

The breaker has no auto-reset, so it latched for 21 hours. The next session
produced 59 candidates and 31 full-consensus EXECUTE decisions; every one was
blocked and zero orders were placed.

The fix publishes the first heartbeat when the loop is actually entered, so the
heartbeat clock measures loop liveness rather than process startup.

Scope note: these tests pin that the false positive is gone and that the
heartbeat guard is otherwise untouched. They do NOT claim a wedged ingestion
loop trips the breaker — it does not, and did not before this fix either, since
`run()`'s tail refreshes the heartbeat immediately before checking it and a
hung cycle reaches neither. A stalled ingestion is caught elsewhere: execution
blocks new entries once `global_health` exceeds `ingestion_heartbeat_max_age`,
and the dead-man watchdog alerts on the ingestion liveness budget.
"""

from __future__ import annotations

import os

os.environ["DB_URL"] = "sqlite+aiosqlite:///:memory:"

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.core import health_monitor as hm
from orion.core.circuit_breaker import CircuitBreaker

pytestmark = pytest.mark.integration

# The observed hydration window from the incident: longer than the 60s heartbeat
# threshold, but under the 600s machine-sleep escape hatch, so it trips.
HYDRATION_SECONDS = 425.76


class _FakeClock:
    """Stands in for the `time` module inside orion.core.health_monitor.

    Fixed, injected timestamps only — the assertions must never depend on the
    wall clock.
    """

    def __init__(self, start: float = 1_000_000.0) -> None:
        self._now = start

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _make_service():
    """Build an IngestionService with a REAL HealthMonitor.

    Everything else the constructor touches is stubbed; the heartbeat is the
    subject under test, so it must not be mocked away.
    """
    with (
        patch("orion.ingestion.service.UniverseManager"),
        patch("orion.ingestion.service.FeatureEngine"),
        patch("orion.ingestion.service.RuleEngine"),
        patch("orion.ingestion.service.xcals"),
        patch("orion.ingestion.service.create_gateway_stream_client") as mock_factory,
    ):
        mock_factory.return_value = MagicMock()
        from orion.ingestion.service import IngestionService

        return IngestionService()


async def test_slow_startup_does_not_leave_breaker_latched_open(monkeypatch):
    """The incident trace: ~425s of startup, then the market-closed EOD path
    calls check_health() before any heartbeat has been published.

    Trading must not be disabled. This is the regression guard.
    """
    clock = _FakeClock()
    monkeypatch.setattr(hm, "time", clock)

    svc = _make_service()

    async def slow_initialize() -> None:
        # universe hydrate + feature-engine history hydrate
        clock.advance(HYDRATION_SECONDS)

    svc.initialize = AsyncMock(side_effect=slow_initialize)
    svc._handle_shutdown_signals = MagicMock()
    svc.stop = AsyncMock()

    tripped: list[str] = []

    async def first_cycle() -> None:
        # Reproduces _check_overnight_sleep() -> _maybe_run_eod() ->
        # _update_health_status(), which runs BEFORE _run_cycle's own
        # update_heartbeat() on the market-closed path.
        try:
            await svc.health_monitor.check_health()
        except hm.CriticalHealthError as exc:
            tripped.append(str(exc))
        svc.shutdown_event.set()

    svc._run_cycle = AsyncMock(side_effect=first_cycle)

    await svc.run()

    assert tripped == [], f"startup was measured as a liveness gap: {tripped}"
    assert await CircuitBreaker().is_open() is False


async def test_run_publishes_a_heartbeat_before_the_first_cycle(monkeypatch):
    """Pins the fix at its location rather than through its side effect: the
    heartbeat clock must be started on loop entry, so it can never measure
    initialize()'s duration no matter how slow startup gets."""
    clock = _FakeClock()
    monkeypatch.setattr(hm, "time", clock)

    svc = _make_service()
    heartbeat_at_first_cycle: list[float] = []

    async def slow_initialize() -> None:
        clock.advance(HYDRATION_SECONDS)

    svc.initialize = AsyncMock(side_effect=slow_initialize)
    svc._handle_shutdown_signals = MagicMock()
    svc.stop = AsyncMock()

    async def first_cycle() -> None:
        heartbeat_at_first_cycle.append(svc.health_monitor.last_heartbeat_ts)
        svc.shutdown_event.set()

    svc._run_cycle = AsyncMock(side_effect=first_cycle)

    await svc.run()

    assert heartbeat_at_first_cycle == [clock.time()]


async def test_heartbeat_guard_still_trips_the_breaker_when_it_fires(monkeypatch):
    """Non-regression on the guard itself: once a real heartbeat exists and then
    goes stale, check_health() still trips and latches. The fix must remove the
    false positive, not the protection.

    This exercises HealthMonitor directly, which is the honest scope — see the
    module docstring for why it is not evidence that a wedged service loop
    reaches this code."""
    clock = _FakeClock()
    monkeypatch.setattr(hm, "time", clock)

    monitor = hm.HealthMonitor()
    monitor.update_heartbeat()  # a real, published heartbeat
    clock.advance(HYDRATION_SECONDS)  # ...then the loop goes quiet

    with pytest.raises(hm.CriticalHealthError):
        await monitor.check_health()

    assert await CircuitBreaker().is_open() is True


async def test_breaker_opened_by_a_persistent_fault_still_latches(monkeypatch):
    """A breaker opened for a non-heartbeat reason (drawdown kill switch, lag,
    manual admin halt) must stay OPEN and require a human — nothing in this fix
    may auto-clear it."""
    clock = _FakeClock()
    monkeypatch.setattr(hm, "time", clock)

    breaker = CircuitBreaker()
    await breaker.open("CRITICAL: Drawdown kill switch — equity 10100 vs peak 100000")

    monitor = hm.HealthMonitor()
    monitor.update_heartbeat()
    await monitor.check_health()  # heartbeat is fresh; must not raise

    assert await breaker.is_open() is True
    state = await breaker.get_state()
    assert "Drawdown kill switch" in state["reason"]


async def test_startup_cannot_clear_a_latched_breaker():
    """No configuration may let a restart re-enable order flow.

    `ORION_RESET_CIRCUIT_BREAKER_ON_START` closed the breaker during
    `initialize()` — an escape hatch for exactly the false positive fixed above,
    but one that also cleared genuine latches (drawdown kill switch, manual
    halt). With the false positive gone the hatch is pure fail-open risk: a
    persistent fault must survive a restart and wait for a human.
    """
    from orion.config import system_settings

    # The escape hatch must not exist at all: while the setting is present, an
    # env var can re-arm the fail-open path no matter what the default is.
    assert not hasattr(system_settings, "reset_circuit_breaker_on_start")

    breaker = CircuitBreaker()
    await breaker.open("CRITICAL: Drawdown kill switch — equity 10100 vs peak 100000")

    svc = _make_service()
    svc.universe.hydrate_from_db = AsyncMock()
    svc.feature_engine.hydrate_history = AsyncMock()
    svc.gateway_stream.start = AsyncMock()
    svc.gateway_stream.subscribe = AsyncMock()
    svc.gateway_stream.subscribe_flow = AsyncMock()

    with (
        patch("orion.ingestion.service.wait_for_db", AsyncMock()),
        patch("orion.ingestion.service.init_db", AsyncMock()),
        patch("orion.ingestion.service.acquire_service_lease", AsyncMock(return_value="run-1")),
        patch("orion.jobs.rollup_job.RollupJob"),
    ):
        await svc.initialize()

    assert await breaker.is_open() is True
    assert "Drawdown kill switch" in (await breaker.get_state())["reason"]
