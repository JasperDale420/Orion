"""Per-service single-instance lease.

A soft single-process guard backed by ``SystemStatus`` rows keyed
``service_lease_<service_id>``. Each long-running Orion service that
must run at most once concurrently calls
``acquire_service_lease("<service_id>")`` at startup and periodically
``renew_service_lease(service_id, run_id)`` on a heartbeat so other
processes treat the lease as live.

H-05 from predict 260430-1751-execution-reliability flagged that the
single-process invariant — load-bearing for execution state like
``pending_orders``, ``processed_fill_ids``, ``_partial_fill_tracker``,
``_closing_symbols`` — was undocumented and unenforced. Until 2026-05-20
the lease lived as instance methods on ``ExecutionEngine`` and was only
used by ``main_execution``. Extracting it as free functions lets the
ingestion service (and any other future service) reuse the exact same
guarantee without instantiating an ExecutionEngine.

This module is the canonical home of the lease logic.
``ExecutionEngine.acquire_service_lease`` / ``renew_service_lease``
are thin wrappers preserved for backward compatibility with existing
tests and call sites.

Identity: lease ownership uses ``ORION_LEASE_OWNER_ID`` when set
(typically a stable deployment identifier like
``orion_execution_compose``) so a container restart re-acquires its
own lease without waiting out the stale window. Falls back to a
per-process uuid4 when unset, preserving the original "two
simultaneous starts both new" guard for ad-hoc CLI invocations.

Safety: a check-then-write race between two simultaneous starts can
let both through. Acceptable for Orion's single-Docker/native-process
deployment; the goal is to catch the common accidental cases
(forgotten canary, local-while-prod, native-while-compose,
double-deploy). Not a distributed lock.
"""

from __future__ import annotations

import os
import socket
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from orion.shared.logger import setup_struct_logger
from orion.shared.utils import ensure_utc
from orion.storage.db import async_session_factory

logger = setup_struct_logger(__name__)

SERVICE_LEASE_KEY_PREFIX = "service_lease_"
SERVICE_LEASE_STALE_SECONDS = 120


class ServiceLeaseLostError(RuntimeError):
    """The lease is confirmably held by another live instance.

    Raised by ``renew_service_lease`` only for callers that opt in with
    ``fence_on_confirmed_loss=True``, and only on CONFIRMED loss: the row
    exists, carries a different run_id, and is fresh. A DB error, a missing
    row, a foreign row that has itself gone stale, and our own stale row are
    all doubt, not proof, and stay non-fatal.

    Fatal means the process must exit non-zero so its supervisor relaunches
    it — the relaunched instance re-acquires only if the lease is genuinely
    free or stale.
    """


def _service_lease_key(service_id: str) -> str:
    return f"{SERVICE_LEASE_KEY_PREFIX}{service_id}"


def _resolve_run_id() -> str:
    """Pick the lease owner id from env, else mint a fresh uuid4."""
    env_owner = os.environ.get("ORION_LEASE_OWNER_ID", "").strip()
    return env_owner or str(uuid.uuid4())


def _format_details(run_id: str) -> str:
    return f"run_id={run_id} host={socket.gethostname()} pid={os.getpid()}"


def _is_fresh(last_updated_utc: datetime | None) -> bool:
    """True when the row was heartbeated within the stale window.

    An absent timestamp reads as NOT fresh: it is not evidence of a live
    holder, and fencing is only ever justified by evidence.

    A FUTURE timestamp reads as not fresh either. A negative age is clock
    skew or a stray fixture, not a heartbeat — and accepting it would fence a
    healthy process whose relaunch is then refused the lease on the same
    reading, a crash loop lasting until the timestamp ages out.
    """
    if last_updated_utc is None:
        return False
    last_utc = ensure_utc(last_updated_utc)
    if last_utc is None:
        return False
    age = (datetime.now(UTC) - last_utc).total_seconds()
    return 0.0 <= age < SERVICE_LEASE_STALE_SECONDS


async def acquire_service_lease(service_id: str) -> str:
    """Refuse to start if another instance of ``service_id`` holds a fresh lease.

    Backed by a ``SystemStatus`` row at ``service_lease_<service_id>``. A
    row is considered "fresh" if its ``last_updated_utc`` is within the
    last ``SERVICE_LEASE_STALE_SECONDS`` — anything older is presumed to
    be a prior crashed instance and overwritten.

    Returns the ``run_id`` the caller should pass to
    ``renew_service_lease`` on its heartbeat. Two concurrent instances
    sharing the same ``ORION_LEASE_OWNER_ID`` will both think they own
    the lease — acceptable under docker-compose which serializes
    container start/stop, but the calling deployment MUST give distinct
    owner ids to mutually-exclusive variants (e.g.
    ``orion_ingestion_compose`` vs ``orion_ingestion_native``).

    Raises ``RuntimeError`` if a fresh lease is owned by a different
    run_id. The caller (typically a ``main_*.py`` or
    ``IngestionService.initialize``) should propagate so the process
    exits non-zero.
    """
    from orion.storage.models import SystemStatus

    run_id = _resolve_run_id()
    details = _format_details(run_id)
    key = _service_lease_key(service_id)

    async def _claim(session: Any) -> None:
        stmt = select(SystemStatus).where(SystemStatus.key == key)
        existing = (await session.execute(stmt)).scalars().first()

        now = datetime.now(UTC)
        if existing is not None:
            last = existing.last_updated_utc
            if last is not None:
                last_utc = ensure_utc(last)
                assert last_utc is not None  # last is non-None here
                age = (now - last_utc).total_seconds()
                is_fresh = age < SERVICE_LEASE_STALE_SECONDS
                is_other = f"run_id={run_id}" not in (existing.details or "")
                if is_fresh and is_other:
                    raise RuntimeError(
                        f"Another '{service_id}' instance holds a fresh lease "
                        f"(age={age:.1f}s, details={existing.details!r}). "
                        f"Refusing to start to preserve the single-process invariant."
                    )
            existing.status = "RUNNING"
            existing.details = details
            existing.last_updated_utc = now
        else:
            session.add(
                SystemStatus(
                    key=key,
                    status="RUNNING",
                    details=details,
                    last_updated_utc=now,
                )
            )

    async with async_session_factory() as session:
        await _claim(session)
        await session.commit()

    logger.info(
        "service_lease_acquired",
        extra={"event_type": "SERVICE_LEASE_ACQUIRED", "service_id": service_id, "run_id": run_id},
    )
    return run_id


async def renew_service_lease(service_id: str, run_id: str, *, fence_on_confirmed_loss: bool = False) -> None:
    """Refresh the lease's ``last_updated_utc`` so other processes see it live.

    Defensive: only renews if the row still belongs to ``run_id``. If
    another process has taken over (we hung past the stale threshold
    and they legitimately claimed it), logs ``service_lease_lost`` and
    returns without overwriting their row.

    With ``fence_on_confirmed_loss=True``, a CONFIRMED takeover — the row
    exists, carries a different run_id, and is fresh — instead logs CRITICAL
    ``SERVICE_LEASE_LOST_FENCING`` and raises ``ServiceLeaseLostError`` so the
    caller can exit rather than carry on as a second writer. Callers that do
    their own confirmation (``position_monitor``, ``data_quality`` re-read the
    row themselves) leave it off and keep the log-and-return contract.

    A foreign row that has itself gone stale is NOT confirmed loss: whoever
    took it is no longer heartbeating, so there is nobody live to yield to.
    Neither is our own stale row — being slow to renew is not losing it.

    DB errors are logged but do not propagate — a transient DB blip
    must not abort the caller's main loop. The next heartbeat retries.
    Repeated failures naturally let the lease go stale so another
    process can take over (defensive correctness, not perfect
    liveness).
    """
    from orion.storage.models import SystemStatus

    details = _format_details(run_id)
    key = _service_lease_key(service_id)

    try:
        async with async_session_factory() as session:
            stmt = select(SystemStatus).where(SystemStatus.key == key)
            existing = (await session.execute(stmt)).scalars().first()
            if existing is not None:
                if f"run_id={run_id}" not in (existing.details or ""):
                    if fence_on_confirmed_loss and _is_fresh(existing.last_updated_utc):
                        logger.critical(
                            "service_lease_lost_fencing",
                            extra={
                                "event_type": "SERVICE_LEASE_LOST_FENCING",
                                "service_id": service_id,
                                "run_id": run_id,
                                "current_details": existing.details,
                            },
                        )
                        raise ServiceLeaseLostError(
                            f"'{service_id}' lease (run_id={run_id}) is held by another live instance "
                            f"({existing.details!r}). Exiting to preserve the single-process invariant."
                        )
                    logger.warning(
                        "service_lease_lost",
                        extra={
                            "event_type": "SERVICE_LEASE_LOST",
                            "service_id": service_id,
                            "current_details": existing.details,
                            # Surfaced so a foreign row that is stale — or
                            # future-dated by clock skew — is diagnosable
                            # rather than an unexplained absence of fencing.
                            "current_last_updated_utc": str(existing.last_updated_utc),
                        },
                    )
                    return
                existing.last_updated_utc = datetime.now(UTC)
                existing.details = details
            else:
                session.add(
                    SystemStatus(
                        key=key,
                        status="RUNNING",
                        details=details,
                        last_updated_utc=datetime.now(UTC),
                    )
                )
            await session.commit()
    except ServiceLeaseLostError:
        # Proof of a live competitor, not a transient failure — never swallowed.
        raise
    except Exception as exc:
        logger.warning(
            "service_lease_renew_failed",
            extra={"event_type": "SERVICE_LEASE_RENEW_FAILED", "service_id": service_id, "error": str(exc)},
        )
