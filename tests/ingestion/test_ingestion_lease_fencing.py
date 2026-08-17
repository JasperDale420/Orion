"""Ingestion must stop when its single-instance lease is confirmably taken.

Ingestion renewed through the shared helper, which swallowed a takeover, so a
displaced instance kept writing bronze/silver and running EOD reconcile against
the same tables as the winner. Position monitor already treats loss as fatal;
ingestion now does too, from both places it renews — the run() heartbeat tail
and the market-closed sleep loop that parks over a weekend.

Fatal means raise, not just set the shutdown event: a clean exit-0 looks like a
successful shutdown to launchd. Raising exits non-zero, and the relaunched
process re-acquires only if the lease is genuinely free or stale.
"""

import os

os.environ["DB_URL"] = "sqlite+aiosqlite:///:memory:"

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.core.service_lease import ServiceLeaseLostError

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _make_service():
    with (
        patch("orion.ingestion.service.HealthMonitor"),
        patch("orion.ingestion.service.UniverseManager"),
        patch("orion.ingestion.service.FeatureEngine"),
        patch("orion.ingestion.service.RuleEngine"),
        patch("orion.ingestion.service.xcals"),
        patch("orion.ingestion.service.create_gateway_stream_client") as mock_factory,
    ):
        mock_factory.return_value = MagicMock()
        from orion.ingestion.service import IngestionService

        svc = IngestionService()

    svc.initialize = AsyncMock()
    svc._handle_shutdown_signals = MagicMock()
    svc.stop = AsyncMock()
    svc._update_health_status = AsyncMock()
    return svc


async def test_maybe_renew_lease_opts_into_fencing() -> None:
    svc = _make_service()
    svc._lease_run_id = "ours"

    with patch("orion.ingestion.service.renew_service_lease", new=AsyncMock()) as renew:
        await svc._maybe_renew_lease()

    assert renew.await_args.kwargs["fence_on_confirmed_loss"] is True


async def test_run_exits_non_zero_when_the_heartbeat_finds_the_lease_taken() -> None:
    svc = _make_service()
    svc._run_cycle = AsyncMock()
    svc._maybe_renew_lease = AsyncMock(side_effect=ServiceLeaseLostError("taken"))

    with pytest.raises(ServiceLeaseLostError):
        await svc.run()

    assert svc.shutdown_event.is_set()


async def test_run_exits_non_zero_when_the_cycle_finds_the_lease_taken() -> None:
    """The overnight sleep loop renews from inside `_run_cycle`; that path must
    not be swallowed by the loop's catch-all error handler."""
    svc = _make_service()
    svc._run_cycle = AsyncMock(side_effect=ServiceLeaseLostError("taken"))
    svc._maybe_renew_lease = AsyncMock()

    with pytest.raises(ServiceLeaseLostError):
        await svc.run()

    assert svc.shutdown_event.is_set()


async def test_overnight_sleep_loop_propagates_a_fenced_lease() -> None:
    """Pins the fix at the sleep loop itself, not only at run()'s handler."""
    svc = _make_service()
    svc._maybe_run_eod = AsyncMock()
    svc._maybe_renew_lease = AsyncMock(side_effect=ServiceLeaseLostError("taken"))

    schedule = MagicMock()
    schedule.is_market_open.return_value = False
    schedule.seconds_until_open.return_value = 0.01

    with (
        patch("orion.core.market_schedule.MarketSchedule", return_value=schedule),
        pytest.raises(ServiceLeaseLostError),
    ):
        await svc._check_overnight_sleep()


async def test_a_transient_renewal_error_does_not_stop_the_loop() -> None:
    """Only a confirmed takeover is fatal. Anything else keeps today's
    swallow-and-retry behaviour."""
    svc = _make_service()
    cycles = 0

    async def one_cycle_then_quit() -> None:
        nonlocal cycles
        cycles += 1
        if cycles >= 2:
            svc.shutdown_event.set()

    svc._run_cycle = AsyncMock(side_effect=one_cycle_then_quit)
    svc._maybe_renew_lease = AsyncMock(side_effect=RuntimeError("transient DB blip"))

    await svc.run()

    assert cycles >= 2
    svc.stop.assert_awaited()
