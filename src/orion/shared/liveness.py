"""Unified service-liveness publisher (redesign R3 / task RA.1).

A long-running service calls :func:`publish_liveness` at the end of every
successful work cycle. It performs a single upsert into ``service_liveness``,
advancing ``last_success_ts_utc`` to now and incrementing ``cycle_count``.

Swallow-never-crash semantics: liveness is observability, not control flow. A
transient DB error here must never break a trading loop — so every failure is
caught and debug-logged, exactly like the service-lease renewal path. The
watchdog already treats a *missing* update as the alert condition, so a swallowed
publish degrades gracefully into the very signal we want.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Insert

from orion.shared.logger import setup_struct_logger
from orion.storage.db import async_session_factory
from orion.storage.models_liveness import ServiceLiveness

logger = setup_struct_logger("orion.liveness")


def _build_upsert(dialect_name: str, params: dict[str, Any]) -> Insert:
    """Build a dialect-appropriate ON CONFLICT upsert statement.

    On conflict (the service row already exists) we advance the timestamps and
    declared budget, overwrite ``last_error`` with this cycle's value, and
    increment the persisted ``cycle_count`` by one. Only SQLite (tests) and
    PostgreSQL (production) are supported; an unknown dialect raises so the
    caller's swallow path logs it rather than silently no-op'ing.
    """
    if dialect_name not in ("postgresql", "sqlite"):
        raise ValueError(f"unsupported dialect for liveness upsert: {dialect_name}")
    insert_fn = pg_insert if dialect_name == "postgresql" else sqlite_insert

    stmt = insert_fn(ServiceLiveness).values(
        service=params["service"],
        last_success_ts_utc=params["now"],
        cycle_count=1,
        last_error=params["error"],
        cadence_budget_seconds=params["cadence_budget_seconds"],
        updated_at=params["now"],
    )
    return stmt.on_conflict_do_update(
        index_elements=[ServiceLiveness.service],
        set_={
            "last_success_ts_utc": params["now"],
            "last_error": params["error"],
            "cadence_budget_seconds": params["cadence_budget_seconds"],
            "updated_at": params["now"],
            "cycle_count": ServiceLiveness.cycle_count + 1,
        },
    )


async def publish_liveness(
    service: str,
    *,
    cadence_budget_seconds: int,
    error: str | None = None,
) -> None:
    """Record a successful cycle for ``service`` (single upsert; never raises).

    ``cadence_budget_seconds`` is the service's own declared staleness budget,
    persisted so the watchdog reads the budget straight off the row. ``error``
    is an optional last-error string surfaced for diagnostics; a healthy cycle
    passes ``None`` (which clears any prior error).
    """
    now = datetime.now(UTC)
    params: dict[str, Any] = {
        "service": service,
        "now": now,
        "error": error,
        "cadence_budget_seconds": cadence_budget_seconds,
    }

    async def _write() -> None:
        session: AsyncSession = async_session_factory()
        async with session:
            if error is not None:
                # Error publish: record the failure WITHOUT advancing
                # last_success_ts_utc/cycle_count (adversarial-review finding:
                # a liveness row that advances during a broken cycle defeats
                # the dead-man design). Update-only — a service that has never
                # registered a success has no row, and an error no-ops.
                await session.execute(
                    update(ServiceLiveness)
                    .where(ServiceLiveness.service == service)
                    .values(last_error=error, updated_at=now)
                )
            else:
                dialect_name = session.bind.dialect.name if session.bind is not None else "postgresql"
                await session.execute(_build_upsert(dialect_name, params))
            await session.commit()

    try:
        # Hard timeout: never-raises is not never-blocks — a hung DB must not
        # freeze a trading loop at an observability call.
        await asyncio.wait_for(_write(), timeout=5.0)
    except Exception as exc:  # noqa: BLE001 — liveness must never break a loop
        logger.debug(
            "liveness_publish_failed",
            service=service,
            error=str(exc),
        )
