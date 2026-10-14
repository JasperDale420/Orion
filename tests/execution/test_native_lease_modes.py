"""Lease-acquisition semantics for the native position-monitor and
data-quality entrypoints (Wave C RB.4).

These pin the plan's lease/parity interaction rule:

  - position-monitor `--dry-run` (read-only: cannot submit closes) SKIPS
    lease acquisition, so the W6 `--dry-run --once` parity gate can run
    while the docker copy still holds the lease.
  - position-monitor `--once` WITHOUT `--dry-run` is execute-capable
    (run_check(dry_run=False) can submit closes), so it ACQUIRES the lease.
  - the position-monitor live daemon (no flags) ACQUIRES the lease.
  - data-quality `--scheduled` daemon ACQUIRES the lease; a plain one-shot
    `run_once()` does NOT.
  - a held fresh lease from a different owner makes acquisition RAISE
    (existing service_lease semantics).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion import main_data_quality, main_position_monitor
from orion.core.service_lease import (
    SERVICE_LEASE_KEY_PREFIX,
    acquire_service_lease,
)
from orion.storage.db import async_session_factory
from orion.storage.models import SystemStatus


def _pm_argv(*flags: str) -> list[str]:
    return ["main_position_monitor", *flags]


def _dq_argv(*flags: str) -> list[str]:
    return ["main_data_quality", *flags]


async def _plant_foreign_lease(service_id: str) -> None:
    """Insert a fresh lease owned by a different run_id so a subsequent
    acquire from this process must refuse."""
    from datetime import UTC, datetime

    async with async_session_factory() as session:
        session.add(
            SystemStatus(
                key=f"{SERVICE_LEASE_KEY_PREFIX}{service_id}",
                status="RUNNING",
                details="run_id=ffffffff-ffff-ffff-ffff-ffffffffffff host=other pid=9999",
                last_updated_utc=datetime.now(UTC),
            )
        )
        await session.commit()


@pytest.fixture
def patched_pm():
    """Patch position-monitor's heavy deps so only lease behavior is exercised."""
    monitor = MagicMock()
    monitor.run_check = AsyncMock(return_value={"positions_checked": 0})

    with (
        patch.object(main_position_monitor, "_build_execution_engine", AsyncMock(return_value=MagicMock())),
        patch.object(main_position_monitor, "get_gateway_trading_client", MagicMock(return_value=MagicMock())),
        patch.object(main_position_monitor, "get_position_monitor", MagicMock(return_value=monitor)),
        patch.object(main_position_monitor, "GatewayPositionAdapter") as adapter_cls,
        patch.object(main_position_monitor, "run_position_monitor_loop", AsyncMock()) as loop_mock,
        patch.object(main_position_monitor, "acquire_service_lease", AsyncMock(return_value="run-1")) as acquire_mock,
    ):
        adapter = MagicMock()
        adapter.refresh = AsyncMock()
        adapter_cls.return_value = adapter
        yield {"acquire": acquire_mock, "loop": loop_mock, "run_check": monitor.run_check}


@pytest.mark.integration
@pytest.mark.asyncio
class TestPositionMonitorLeaseModes:
    async def test_dry_run_once_skips_lease(self, patched_pm, monkeypatch) -> None:
        monkeypatch.setattr("sys.argv", _pm_argv("--dry-run", "--once"))
        await main_position_monitor.main()
        patched_pm["acquire"].assert_not_called()
        patched_pm["run_check"].assert_awaited_once()

    async def test_dry_run_daemon_skips_lease(self, patched_pm, monkeypatch) -> None:
        monkeypatch.setattr("sys.argv", _pm_argv("--dry-run"))
        await main_position_monitor.main()
        patched_pm["acquire"].assert_not_called()
        patched_pm["loop"].assert_awaited_once()

    async def test_bare_once_acquires_lease(self, patched_pm, monkeypatch) -> None:
        # `--once` alone runs run_check(dry_run=False) which can submit closes,
        # so it MUST take the lease.
        monkeypatch.setattr("sys.argv", _pm_argv("--once"))
        await main_position_monitor.main()
        patched_pm["acquire"].assert_awaited_once_with("position_monitor")
        patched_pm["run_check"].assert_awaited_once()

    async def test_live_daemon_acquires_lease(self, patched_pm, monkeypatch) -> None:
        monkeypatch.setattr("sys.argv", _pm_argv())
        await main_position_monitor.main()
        patched_pm["acquire"].assert_awaited_once_with("position_monitor")
        patched_pm["loop"].assert_awaited_once()

    async def test_held_lease_by_other_owner_raises(self, monkeypatch) -> None:
        # Real lease layer (no acquire patch): a foreign fresh lease blocks
        # the execute-capable bare `--once` start.
        await _plant_foreign_lease("position_monitor")

        with (
            patch.object(main_position_monitor, "_build_execution_engine", AsyncMock(return_value=MagicMock())),
            patch.object(main_position_monitor, "get_gateway_trading_client", MagicMock(return_value=MagicMock())),
            patch.object(main_position_monitor, "get_position_monitor", MagicMock()),
            patch.object(main_position_monitor, "GatewayPositionAdapter", MagicMock()),
        ):
            monkeypatch.setattr("sys.argv", _pm_argv("--once"))
            with pytest.raises(RuntimeError, match="holds a fresh lease"):
                await main_position_monitor.main()


@pytest.mark.integration
@pytest.mark.asyncio
class TestDataQualityLeaseModes:
    async def test_scheduled_daemon_acquires_lease(self, monkeypatch) -> None:
        import asyncio

        with (
            # run_scheduled now waits for the DB before init_db; the pre-set
            # shutdown event below would otherwise trip its shutdown-abort.
            patch.object(main_data_quality, "wait_for_db", AsyncMock()),
            patch.object(main_data_quality, "acquire_service_lease", AsyncMock(return_value="run-2")) as acquire_mock,
            patch.object(main_data_quality, "run_quality_checks", AsyncMock(return_value={})),
            patch.object(main_data_quality, "_is_market_hours", MagicMock(return_value=False)),
        ):
            shutdown = asyncio.Event()
            shutdown.set()  # exit the loop immediately after lease acquisition
            await main_data_quality.run_scheduled(shutdown)
            acquire_mock.assert_awaited_once_with("data_quality")

    async def test_run_once_does_not_acquire_lease(self, monkeypatch) -> None:
        with (
            patch.object(main_data_quality, "acquire_service_lease", AsyncMock()) as acquire_mock,
            patch.object(main_data_quality, "run_quality_checks", AsyncMock(return_value={})),
        ):
            await main_data_quality.run_once()
            acquire_mock.assert_not_called()

    async def test_held_lease_by_other_owner_raises(self) -> None:
        import asyncio

        await _plant_foreign_lease("data_quality")
        with (
            patch.object(main_data_quality, "wait_for_db", AsyncMock()),
            patch.object(main_data_quality, "run_quality_checks", AsyncMock(return_value={})),
        ):
            shutdown = asyncio.Event()
            shutdown.set()
            with pytest.raises(RuntimeError, match="holds a fresh lease"):
                await main_data_quality.run_scheduled(shutdown)


@pytest.mark.integration
@pytest.mark.asyncio
class TestPositionMonitorLeaseLossIsFatal:
    """Finding 3: in execute-capable modes the renewal task must start before
    heavy init, and a lost/stolen lease must hard-stop the monitor (no close
    can be submitted after the lease is known lost)."""

    async def test_renewal_starts_before_engine_init(self, monkeypatch) -> None:
        # Record the order in which the renewal task body and the (slow) engine
        # init first run. _run_with_lease_guard creates the renew task before it
        # awaits _do_work, so the renewal coroutine must be scheduled first.
        import asyncio

        order: list[str] = []

        async def fake_renew(run_id: str) -> None:
            order.append("renew")
            # Sleep forever; the guard cancels us when work completes.
            await asyncio.Event().wait()

        async def fake_build_engine() -> MagicMock:
            await asyncio.sleep(0)  # yield so a racing renew task can run first
            order.append("engine_init")
            return MagicMock()

        with (
            patch.object(main_position_monitor, "_renew_lease_forever", fake_renew),
            patch.object(main_position_monitor, "_build_execution_engine", fake_build_engine),
            patch.object(main_position_monitor, "get_gateway_trading_client", MagicMock(return_value=MagicMock())),
            patch.object(main_position_monitor, "run_position_monitor_loop", AsyncMock()) as loop_mock,
            patch.object(main_position_monitor, "acquire_service_lease", AsyncMock(return_value="run-1")),
            patch.object(main_position_monitor, "init_db", AsyncMock()),
        ):
            monkeypatch.setattr("sys.argv", _pm_argv())
            await main_position_monitor.main()

        assert order[0] == "renew", f"renewal must start before engine init, got {order}"
        assert "engine_init" in order
        loop_mock.assert_awaited_once()

    async def test_lease_loss_stops_loop_no_further_checks(self, monkeypatch) -> None:
        # Mock the renewal task to fail (lease stolen). The guard must cancel the
        # running loop and propagate LeaseLostError — proving no further check
        # iterations run after loss.
        import asyncio

        loop_iterations = 0
        loop_cancelled = asyncio.Event()

        async def long_running_loop(**_kwargs) -> None:
            nonlocal loop_iterations
            try:
                while True:
                    loop_iterations += 1
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                loop_cancelled.set()
                raise

        async def failing_renew(run_id: str) -> None:
            await asyncio.sleep(0.02)  # let the loop spin a couple of times first
            raise main_position_monitor.LeaseLostError("lease stolen in test")

        with (
            patch.object(main_position_monitor, "_renew_lease_forever", failing_renew),
            patch.object(main_position_monitor, "_build_execution_engine", AsyncMock(return_value=MagicMock())),
            patch.object(main_position_monitor, "get_gateway_trading_client", MagicMock(return_value=MagicMock())),
            patch.object(main_position_monitor, "run_position_monitor_loop", long_running_loop),
            patch.object(main_position_monitor, "acquire_service_lease", AsyncMock(return_value="run-1")),
            patch.object(main_position_monitor, "init_db", AsyncMock()),
        ):
            monkeypatch.setattr("sys.argv", _pm_argv())
            with pytest.raises(main_position_monitor.LeaseLostError, match="lease stolen"):
                await main_position_monitor.main()

        assert loop_cancelled.is_set(), "loop must be cancelled on lease loss"
        iterations_at_cancel = loop_iterations
        # The loop is cancelled; no new iterations may occur afterward.
        await asyncio.sleep(0.05)
        assert loop_iterations == iterations_at_cancel, "no further checks may run after lease loss"


@pytest.mark.integration
@pytest.mark.asyncio
class TestDataQualityLeaseLossIsFatal:
    async def test_scheduled_lease_loss_stops_and_raises(self, monkeypatch) -> None:
        # Real _renew_lease_forever path: plant a foreign lease AFTER acquiring,
        # so the first heartbeat sees a stolen lease, sets the shutdown event,
        # and raises LeaseLostError out of run_scheduled.
        import asyncio

        monkeypatch.setattr(main_data_quality, "_LEASE_RENEW_INTERVAL_SECONDS", 0.01)
        monkeypatch.setattr(main_data_quality, "CHECK_INTERVAL_SECONDS", 0.05)

        with (
            # Ownership check reports "not ours" so we exercise the loss branch
            # deterministically without DB timing races.
            patch.object(main_data_quality, "_lease_is_ours", AsyncMock(return_value=False)),
            patch.object(main_data_quality, "acquire_service_lease", AsyncMock(return_value="run-dq")),
            patch.object(main_data_quality, "renew_service_lease", AsyncMock()),
            patch.object(main_data_quality, "run_quality_checks", AsyncMock(return_value={})),
            patch.object(main_data_quality, "_is_market_hours", MagicMock(return_value=False)),
            patch.object(main_data_quality, "init_db", AsyncMock()),
        ):
            shutdown = asyncio.Event()
            with pytest.raises(main_data_quality.LeaseLostError, match="taken by another owner"):
                await main_data_quality.run_scheduled(shutdown)
            assert shutdown.is_set(), "lease loss must set the shutdown event"


@pytest.mark.integration
@pytest.mark.asyncio
class TestDataQualityLeaseCheckSurvivesTransientDbError:
    async def test_transient_db_error_does_not_stop_the_daemon(self, monkeypatch) -> None:
        # A DB blip during the ownership check (e.g. a Docker VM restart) must
        # be treated as "unknown, retry" — never as confirmed loss (fatal) and
        # never as confirmed ownership (silently continuing unverified). The
        # renew loop should survive the failing tick and resume normal
        # heartbeats once the DB recovers, without ever setting shutdown_event.
        import asyncio

        from sqlalchemy.exc import SQLAlchemyError

        monkeypatch.setattr(main_data_quality, "_LEASE_RENEW_INTERVAL_SECONDS", 0.01)
        monkeypatch.setattr(main_data_quality, "CHECK_INTERVAL_SECONDS", 0.05)

        lease_check = AsyncMock(side_effect=[SQLAlchemyError("db blip"), True, True])

        with (
            patch.object(main_data_quality, "_lease_is_ours", lease_check),
            patch.object(main_data_quality, "acquire_service_lease", AsyncMock(return_value="run-dq")),
            patch.object(main_data_quality, "renew_service_lease", AsyncMock()),
            patch.object(main_data_quality, "run_quality_checks", AsyncMock(return_value={})),
            patch.object(main_data_quality, "_is_market_hours", MagicMock(return_value=False)),
            patch.object(main_data_quality, "init_db", AsyncMock()),
        ):
            shutdown = asyncio.Event()
            task = asyncio.create_task(main_data_quality.run_scheduled(shutdown))
            try:
                # Deterministic wait for both the failing and the following
                # successful ownership check, rather than a fixed sleep.
                for _ in range(500):
                    if lease_check.call_count >= 2:
                        break
                    await asyncio.sleep(0.01)

                assert lease_check.call_count >= 2, "renew loop did not survive past the failing tick"
                assert not shutdown.is_set(), "a transient DB error must not be treated as lease loss"
                assert not task.done(), "the daemon must keep running through a transient DB error"
            finally:
                shutdown.set()
                await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_lease_owner_ids_are_distinct_service_ids() -> None:
    """position_monitor and data_quality use distinct lease service ids, so
    both can hold a lease at once without blocking each other."""
    pm_run = await acquire_service_lease("position_monitor")
    dq_run = await acquire_service_lease("data_quality")
    assert pm_run is not None
    assert dq_run is not None

    async with async_session_factory() as session:
        from sqlalchemy import select

        rows = list(
            (
                await session.execute(
                    select(SystemStatus).where(
                        SystemStatus.key.in_(
                            [
                                f"{SERVICE_LEASE_KEY_PREFIX}position_monitor",
                                f"{SERVICE_LEASE_KEY_PREFIX}data_quality",
                            ]
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 2
