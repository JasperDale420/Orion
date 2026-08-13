"""EOD close-of-books must actually run.

The previous trigger fired only when `now_utc.hour == 1`, but `_run_cycle` calls
`_check_overnight_sleep()` first, which blocks the whole cycle until the next
market open whenever the market is closed. At 01:00 UTC the market is closed, so
the loop was parked in that sleep and the trigger was never reached; during
market hours `hour == 1` is never true. It was unreachable by construction, and
`realize_expired_journal_rows()` — its only caller — never ran, so journal rows
for expired options accumulated until the per-bucket entry caps jammed and Orion
stopped placing orders entirely on 2026-07-31.

The old tests missed this because they called `_check_eod_trigger()` directly.
`test_eod_runs_from_the_overnight_sleep_path` is the regression that matters: it
exercises the real code path rather than the function in isolation.
"""

import os

os.environ["DB_URL"] = "sqlite+aiosqlite:///:memory:"

import asyncio
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.storage.db import async_session_factory, init_db

pytestmark = pytest.mark.unit

# Mon 2026-08-10: NYSE session closes 20:00 UTC (16:00 EDT).
SESSION = date(2026, 8, 10)
CLOSE_UTC = datetime(2026, 8, 10, 20, 0, tzinfo=UTC)


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

    # Liveness side effects are not what these tests are about; the two that
    # DO assert on them re-patch these with their own mocks.
    svc._update_health_status = AsyncMock()
    svc._maybe_renew_lease = AsyncMock()
    return svc


def _schedule(*, market_open: bool, seconds_until_open: float = 36000.0):
    """A MarketSchedule stub pinned to the 2026-08-10 session.

    `seconds_until_open` defaults to a full overnight so the runway guard is
    satisfied; tests that exercise that guard pass a short value.
    """
    sched = MagicMock()
    sched.is_market_open.return_value = market_open
    sched.seconds_until_open.return_value = seconds_until_open
    sched.get_open_close.return_value = (datetime(2026, 8, 10, 13, 30, tzinfo=UTC), CLOSE_UTC)
    return sched


async def _reset_db():
    from sqlalchemy import text

    await init_db()
    async with async_session_factory() as session:
        await session.execute(text("DELETE FROM job_cursor_state"))
        await session.commit()


@pytest.mark.asyncio
async def test_does_not_run_while_market_is_open():
    await _reset_db()
    svc = _make_service()

    with (
        patch("orion.core.market_schedule.MarketSchedule", return_value=_schedule(market_open=True)),
        patch("orion.ingestion.service.datetime") as mock_dt,
        patch.object(svc, "_run_eod_task", new=AsyncMock(return_value=True)) as eod,
    ):
        mock_dt.now.return_value = CLOSE_UTC - timedelta(hours=1)
        await svc._maybe_run_eod()

    eod.assert_not_called()


@pytest.mark.asyncio
async def test_waits_for_the_settlement_grace_period():
    """Immediately at the bell, fills may still be settling."""
    await _reset_db()
    svc = _make_service()

    with (
        patch("orion.core.market_schedule.MarketSchedule", return_value=_schedule(market_open=False)),
        patch("orion.ingestion.service.datetime") as mock_dt,
        patch.object(svc, "_run_eod_task", new=AsyncMock(return_value=True)) as eod,
    ):
        mock_dt.now.return_value = CLOSE_UTC + timedelta(minutes=1)
        await svc._maybe_run_eod()

    eod.assert_not_called()


@pytest.mark.asyncio
async def test_runs_after_close_and_records_the_session_durably():
    await _reset_db()
    svc = _make_service()

    with (
        patch("orion.core.market_schedule.MarketSchedule", return_value=_schedule(market_open=False)),
        patch("orion.ingestion.service.datetime") as mock_dt,
        patch.object(svc, "_run_eod_task", new=AsyncMock(return_value=True)) as eod,
    ):
        mock_dt.now.return_value = CLOSE_UTC + timedelta(minutes=30)
        await svc._maybe_run_eod()

    eod.assert_awaited_once_with(SESSION)
    assert await svc._eod_completed_session() == SESSION


@pytest.mark.asyncio
async def test_does_not_rerun_a_completed_session_after_restart():
    """The marker is durable: a fresh process must not redo the same session."""
    await _reset_db()
    first = _make_service()

    with (
        patch("orion.core.market_schedule.MarketSchedule", return_value=_schedule(market_open=False)),
        patch("orion.ingestion.service.datetime") as mock_dt,
        patch.object(first, "_run_eod_task", new=AsyncMock(return_value=True)),
    ):
        mock_dt.now.return_value = CLOSE_UTC + timedelta(minutes=30)
        await first._maybe_run_eod()

    second = _make_service()  # simulates a restart — no in-memory state carried
    with (
        patch("orion.core.market_schedule.MarketSchedule", return_value=_schedule(market_open=False)),
        patch("orion.ingestion.service.datetime") as mock_dt,
        patch.object(second, "_run_eod_task", new=AsyncMock(return_value=True)) as eod,
    ):
        mock_dt.now.return_value = CLOSE_UTC + timedelta(hours=3)
        await second._maybe_run_eod()

    eod.assert_not_called()


@pytest.mark.asyncio
async def test_failure_does_not_mark_the_session_complete():
    """A failed close-of-books must stay retryable, not be recorded as done."""
    await _reset_db()
    svc = _make_service()

    with (
        patch("orion.core.market_schedule.MarketSchedule", return_value=_schedule(market_open=False)),
        patch("orion.ingestion.service.datetime") as mock_dt,
        patch.object(svc, "_run_eod_task", new=AsyncMock(return_value=False)) as eod,
    ):
        mock_dt.now.return_value = CLOSE_UTC + timedelta(minutes=30)
        await svc._maybe_run_eod()

    eod.assert_awaited_once()
    assert await svc._eod_completed_session() is None


@pytest.mark.asyncio
async def test_failed_run_is_retried_after_the_backoff_interval():
    await _reset_db()
    svc = _make_service()
    from orion.ingestion.service import EOD_RETRY_INTERVAL_SECONDS

    with (
        patch("orion.core.market_schedule.MarketSchedule", return_value=_schedule(market_open=False)),
        patch("orion.ingestion.service.datetime") as mock_dt,
        patch.object(svc, "_run_eod_task", new=AsyncMock(return_value=False)) as eod,
    ):
        mock_dt.now.return_value = CLOSE_UTC + timedelta(minutes=30)
        await svc._maybe_run_eod()
        assert eod.await_count == 1

        # Immediately after: backoff suppresses a second attempt.
        mock_dt.now.return_value = CLOSE_UTC + timedelta(minutes=30, seconds=30)
        await svc._maybe_run_eod()
        assert eod.await_count == 1

        # Once the interval elapses, retry.
        mock_dt.now.return_value = CLOSE_UTC + timedelta(minutes=30, seconds=EOD_RETRY_INTERVAL_SECONDS + 1)
        await svc._maybe_run_eod()
        assert eod.await_count == 2


@pytest.mark.asyncio
async def test_broker_unavailable_reconcile_does_not_close_the_session():
    """A status check, not just "nothing raised".

    `run_reconciliation` returns BROKER_UNAVAILABLE (it does not raise) when the
    broker side is untrusted. Recording that as complete would permanently
    suppress the retry.
    """
    await _reset_db()
    svc = _make_service()
    from orion.jobs.reconcile_pnl import STATUS_BROKER_UNAVAILABLE

    result = MagicMock()
    result.status = STATUS_BROKER_UNAVAILABLE
    result.trade_date = SESSION

    with (
        patch("orion.execution.persistence.realize_expired_journal_rows", new=AsyncMock(return_value=0)),
        patch("orion.jobs.reconcile_pnl.run_reconciliation", new=AsyncMock(return_value=result)),
    ):
        assert await svc._run_eod_task(SESSION) is False


@pytest.mark.asyncio
async def test_expiry_sweep_failure_does_not_close_the_session():
    """The sweep raises on DB failure; a swallowed 0 would read as success."""
    await _reset_db()
    svc = _make_service()

    with patch(
        "orion.execution.persistence.realize_expired_journal_rows",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        assert await svc._run_eod_task(SESSION) is False


@pytest.mark.asyncio
async def test_mismatch_status_still_closes_the_session():
    """MISMATCH means measured-with-drift — the books DID close."""
    await _reset_db()
    svc = _make_service()
    from orion.jobs.reconcile_pnl import STATUS_MISMATCH

    result = MagicMock()
    result.status = STATUS_MISMATCH
    result.trade_date = SESSION

    with (
        patch("orion.execution.persistence.realize_expired_journal_rows", new=AsyncMock(return_value=0)),
        patch("orion.jobs.reconcile_pnl.run_reconciliation", new=AsyncMock(return_value=result)),
        patch("orion.jobs.bucket_metrics.run_bucket_metrics", new=AsyncMock()),
    ):
        assert await svc._run_eod_task(SESSION) is True


@pytest.mark.asyncio
async def test_multi_session_outage_closes_every_missed_session():
    """Down Thu night, back after Fri close: Thursday must not be skipped."""
    await _reset_db()
    svc = _make_service()

    # Cursor stranded on Wed 2026-08-05; target session is Mon 2026-08-10.
    await svc._mark_eod_complete(date(2026, 8, 5), CLOSE_UTC)

    with (
        patch("orion.core.market_schedule.MarketSchedule", return_value=_schedule(market_open=False)),
        patch("orion.ingestion.service.datetime") as mock_dt,
        patch.object(svc, "_run_eod_task", new=AsyncMock(return_value=True)) as eod,
    ):
        mock_dt.now.return_value = CLOSE_UTC + timedelta(minutes=30)
        await svc._maybe_run_eod()

    closed = [c.args[0] for c in eod.await_args_list]
    assert closed == [date(2026, 8, 6), date(2026, 8, 7), date(2026, 8, 10)], closed
    assert await svc._eod_completed_session() == SESSION


@pytest.mark.asyncio
async def test_walk_stops_at_the_first_failure():
    """Advancing past an unclosed session would strand it permanently."""
    await _reset_db()
    svc = _make_service()
    await svc._mark_eod_complete(date(2026, 8, 5), CLOSE_UTC)

    with (
        patch("orion.core.market_schedule.MarketSchedule", return_value=_schedule(market_open=False)),
        patch("orion.ingestion.service.datetime") as mock_dt,
        # Thu 08-06 succeeds, Fri 08-07 fails.
        patch.object(svc, "_run_eod_task", new=AsyncMock(side_effect=[True, False])) as eod,
    ):
        mock_dt.now.return_value = CLOSE_UTC + timedelta(minutes=30)
        await svc._maybe_run_eod()

    assert eod.await_count == 2
    assert await svc._eod_completed_session() == date(2026, 8, 6)


@pytest.mark.asyncio
async def test_replay_refreshes_liveness_between_sessions():
    """N slow targets must not stack into one long liveness gap.

    The per-target timeout bounds a single session; health and the
    single-instance lease have to be refreshed between them, or a multi-session
    replay leaves ingestion looking dead to ExecutionEngine and lets a second
    instance take the lease.
    """
    await _reset_db()
    svc = _make_service()
    await svc._mark_eod_complete(date(2026, 8, 5), CLOSE_UTC)

    with (
        patch("orion.core.market_schedule.MarketSchedule", return_value=_schedule(market_open=False)),
        patch("orion.ingestion.service.datetime") as mock_dt,
        patch.object(svc, "_run_eod_task", new=AsyncMock(return_value=True)) as eod,
        patch.object(svc, "_update_health_status", new=AsyncMock()) as health,
        patch.object(svc, "_maybe_renew_lease", new=AsyncMock()) as lease,
    ):
        mock_dt.now.return_value = CLOSE_UTC + timedelta(minutes=30)
        await svc._maybe_run_eod()

    assert eod.await_count == 3  # Thu, Fri, Mon
    # At least one refresh per target, before and after the work.
    assert health.await_count >= eod.await_count
    assert lease.await_count >= eod.await_count


@pytest.mark.asyncio
async def test_replay_stops_when_the_market_reopens():
    """Close-of-books must never run into a live session."""
    await _reset_db()
    svc = _make_service()
    await svc._mark_eod_complete(date(2026, 8, 5), CLOSE_UTC)

    sched = _schedule(market_open=False)
    # Closed for the initial gate, then reopens before the first target runs.
    sched.is_market_open.side_effect = [False, True]

    with (
        patch("orion.core.market_schedule.MarketSchedule", return_value=sched),
        patch("orion.ingestion.service.datetime") as mock_dt,
        patch.object(svc, "_run_eod_task", new=AsyncMock(return_value=True)) as eod,
        patch.object(svc, "_update_health_status", new=AsyncMock()),
        patch.object(svc, "_maybe_renew_lease", new=AsyncMock()),
    ):
        mock_dt.now.return_value = CLOSE_UTC + timedelta(minutes=30)
        await svc._maybe_run_eod()

    eod.assert_not_called()
    # The unclosed sessions keep their place — cursor unmoved.
    assert await svc._eod_completed_session() == date(2026, 8, 5)


@pytest.mark.asyncio
async def test_target_is_deferred_when_it_could_run_past_the_open():
    """A target must not START if it could still be reconciling at the bell."""
    await _reset_db()
    svc = _make_service()
    from orion.ingestion.service import EOD_MAX_RUNTIME_SECONDS

    from orion.ingestion.service import EOD_BELL_MARGIN_SECONDS

    # Just ABOVE the bounded runtime but inside the bell margin — the boundary
    # case, not a comfortably-short runway.
    sched = _schedule(
        market_open=False,
        seconds_until_open=EOD_MAX_RUNTIME_SECONDS + EOD_BELL_MARGIN_SECONDS - 1,
    )

    with (
        patch("orion.core.market_schedule.MarketSchedule", return_value=sched),
        patch("orion.ingestion.service.datetime") as mock_dt,
        patch.object(svc, "_run_eod_task", new=AsyncMock(return_value=True)) as eod,
    ):
        mock_dt.now.return_value = CLOSE_UTC + timedelta(hours=12)
        await svc._maybe_run_eod()

    eod.assert_not_called()
    assert await svc._eod_completed_session() is None


def test_eod_runtime_bound_stays_inside_the_lease_window():
    """The bound must stay under the lease TTL or a target can outlive its lease."""
    from orion.core.service_lease import SERVICE_LEASE_STALE_SECONDS
    from orion.ingestion.service import EOD_MAX_RUNTIME_SECONDS

    assert EOD_MAX_RUNTIME_SECONDS < SERVICE_LEASE_STALE_SECONDS


@pytest.mark.asyncio
async def test_a_hung_eod_times_out_and_stays_retryable():
    """An unbounded reconcile would stall the sleep loop's health refresh."""
    await _reset_db()
    svc = _make_service()

    async def _never_finishes(_):
        await asyncio.sleep(3600)
        return True

    with (
        patch("orion.core.market_schedule.MarketSchedule", return_value=_schedule(market_open=False)),
        patch("orion.ingestion.service.datetime") as mock_dt,
        patch("orion.ingestion.service.EOD_MAX_RUNTIME_SECONDS", 0.05),
        patch.object(svc, "_run_eod_task", new=_never_finishes),
    ):
        mock_dt.now.return_value = CLOSE_UTC + timedelta(minutes=30)
        await svc._maybe_run_eod()

    assert await svc._eod_completed_session() is None


@pytest.mark.asyncio
async def test_eod_runs_from_the_overnight_sleep_path():
    """THE regression: the EOD must be reachable from the real loop.

    The old trigger sat after `_check_overnight_sleep()` in `_run_cycle`, so the
    market-closed branch never reached it. Drive the actual sleep path and prove
    the close-of-books is invoked.
    """
    await _reset_db()
    svc = _make_service()
    svc.shutdown_event.set()  # collapse the sleep loop to a single pass

    with (
        patch("orion.core.market_schedule.MarketSchedule", return_value=_schedule(market_open=False)),
        patch.object(svc, "_maybe_run_eod", new=AsyncMock()) as maybe_eod,
    ):
        await svc._check_overnight_sleep()

    assert maybe_eod.await_count >= 1, "market-closed path must reach the EOD close-of-books"


@pytest.mark.asyncio
async def test_market_open_sleep_path_skips_eod():
    """The open-market fast path must not do close-of-books work."""
    await _reset_db()
    svc = _make_service()

    with (
        patch("orion.core.market_schedule.MarketSchedule", return_value=_schedule(market_open=True)),
        patch.object(svc, "_maybe_run_eod", new=AsyncMock()) as maybe_eod,
    ):
        await svc._check_overnight_sleep()

    maybe_eod.assert_not_called()
