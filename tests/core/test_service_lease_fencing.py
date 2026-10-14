"""A confirmed lease takeover must fence the old process, not be logged past.

`renew_service_lease` logged `service_lease_lost` and returned when another
owner held the row, so the loser kept running as a concurrent writer — bronze
and silver writes, EOD reconcile and sweep — against a service whose whole
in-memory model assumes one process. A leftover docker ingestion container did
exactly this on 2026-06-08 (345 restarts thrashing the lease).

The distinction that matters is CONFIRMED loss versus doubt:

  * row exists, carries another run_id, and is FRESH  -> someone else is live
    and holding it. Fatal for callers that opt in.
  * anything else — a DB error, a row that vanished, a foreign row that has
    itself gone stale, or our own row that we were merely slow to renew —
    stays non-fatal. Killing a healthy process over a transient blip is its
    own outage.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from orion.core.service_lease import (
    SERVICE_LEASE_STALE_SECONDS,
    ServiceLeaseLostError,
    _service_lease_key,
    acquire_service_lease,
    renew_service_lease,
)
from orion.storage.db import async_session_factory, init_db
from orion.storage.models import SystemStatus

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

FOREIGN = "run_id=ffffffff-ffff-ffff-ffff-ffffffffffff host=other pid=9999"


async def _write_lease(service_id: str, details: str, age_seconds: float) -> None:
    await init_db()
    key = _service_lease_key(service_id)
    async with async_session_factory() as session:
        row = (await session.execute(select(SystemStatus).where(SystemStatus.key == key))).scalars().first()
        ts = datetime.now(UTC) - timedelta(seconds=age_seconds)
        if row is None:
            session.add(SystemStatus(key=key, status="RUNNING", details=details, last_updated_utc=ts))
        else:
            row.details = details
            row.last_updated_utc = ts
        await session.commit()


async def _read_lease(service_id: str) -> SystemStatus | None:
    async with async_session_factory() as session:
        stmt = select(SystemStatus).where(SystemStatus.key == _service_lease_key(service_id))
        return (await session.execute(stmt)).scalars().first()


async def test_fresh_foreign_owner_is_fatal_when_fencing() -> None:
    await _write_lease("fence_fresh", FOREIGN, age_seconds=1.0)

    with pytest.raises(ServiceLeaseLostError, match="fence_fresh"):
        await renew_service_lease("fence_fresh", "ours", fence_on_confirmed_loss=True)

    # The winner's row is left exactly as it was.
    row = await _read_lease("fence_fresh")
    assert row.details == FOREIGN


async def test_fresh_foreign_owner_logs_critical_when_fencing() -> None:
    await _write_lease("fence_log", FOREIGN, age_seconds=1.0)

    fake_logger = MagicMock()
    with patch("orion.core.service_lease.logger", fake_logger), pytest.raises(ServiceLeaseLostError):
        await renew_service_lease("fence_log", "ours", fence_on_confirmed_loss=True)

    fake_logger.critical.assert_called_once()
    assert fake_logger.critical.call_args.kwargs["extra"]["event_type"] == "SERVICE_LEASE_LOST_FENCING"


async def test_fresh_foreign_owner_stays_non_fatal_by_default() -> None:
    """position_monitor and data_quality do their own re-read and raise their
    own error; the shared helper must not change under them."""
    await _write_lease("fence_default", FOREIGN, age_seconds=1.0)

    await renew_service_lease("fence_default", "ours")

    row = await _read_lease("fence_default")
    assert row.details == FOREIGN


async def test_stale_foreign_owner_is_not_a_confirmed_loss() -> None:
    """A foreign row that has itself gone stale is not a live competitor."""
    await _write_lease("fence_stale_foreign", FOREIGN, age_seconds=SERVICE_LEASE_STALE_SECONDS + 60)

    await renew_service_lease("fence_stale_foreign", "ours", fence_on_confirmed_loss=True)


async def test_a_future_dated_foreign_row_does_not_fence_us() -> None:
    """A negative age is clock skew or a stray test fixture, not proof of a
    live competitor. Treating it as fresh would fence a healthy process, and
    the relaunch would then be refused the lease for the same reason — a
    self-inflicted crash loop lasting until the timestamp ages out. Orion has
    been burned by exactly this shape before (future-dated bronze rows written
    into the live DB by a smoke test)."""
    await _write_lease("fence_future", FOREIGN, age_seconds=-86400)

    await renew_service_lease("fence_future", "ours", fence_on_confirmed_loss=True)


async def test_our_own_stale_row_is_renewed_not_fenced() -> None:
    """Being slow to renew our OWN lease is not losing it."""
    run_id = await acquire_service_lease("fence_own_stale")
    await _write_lease("fence_own_stale", f"run_id={run_id} host=x pid=1", SERVICE_LEASE_STALE_SECONDS + 60)

    await renew_service_lease("fence_own_stale", run_id, fence_on_confirmed_loss=True)

    row = await _read_lease("fence_own_stale")
    age = (datetime.now(UTC) - row.last_updated_utc.replace(tzinfo=UTC)).total_seconds()
    assert age < 5.0
    assert f"run_id={run_id}" in row.details


async def test_a_db_error_stays_non_fatal_when_fencing() -> None:
    """A transient blip must not kill the process; the next heartbeat retries."""
    fake_factory = MagicMock()
    fake_factory.return_value.__aenter__.side_effect = RuntimeError("simulated DB failure")

    with patch("orion.core.service_lease.async_session_factory", fake_factory):
        await renew_service_lease("fence_db_error", "ours", fence_on_confirmed_loss=True)


async def test_a_missing_row_is_recreated_not_fenced() -> None:
    await init_db()
    async with async_session_factory() as session:
        row = await session.get(SystemStatus, _service_lease_key("fence_missing"))
        if row is not None:
            await session.delete(row)
        await session.commit()

    await renew_service_lease("fence_missing", "ours", fence_on_confirmed_loss=True)

    row = await _read_lease("fence_missing")
    assert "run_id=ours" in row.details
