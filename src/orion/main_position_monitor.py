"""
Position Monitor CLI.

Runs continuous position monitoring with ML-based exit signals.
Checks open positions and executes exits when triggered.
"""

import argparse
import asyncio
import contextlib

from orion.clients.gateway_trading_client import get_gateway_trading_client
from orion.config import system_settings
from orion.core.service_lease import acquire_service_lease, renew_service_lease
from orion.execution.position_monitor import (
    GatewayPositionAdapter,
    get_position_monitor,
    run_position_monitor_loop,
)
from orion.shared.async_main import run_entrypoint
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db

logger = setup_struct_logger("orion.main_position_monitor")

# Service id for the single-instance lease (SystemStatus row
# `service_lease_position_monitor`). Renewed below at half the stale
# window so the daemon never lets its own lease age out under normal load.
_LEASE_SERVICE_ID = "position_monitor"
_LEASE_RENEW_INTERVAL_SECONDS = 60


class LeaseLostError(RuntimeError):
    """Raised by the renewal task when the lease is no longer ours.

    Fatal in execute-capable modes: a stolen lease means a competing
    instance may now hold it, so this process must stop submitting closes
    immediately. Propagates out of ``main()`` so ``run_entrypoint`` logs a
    CRITICAL and exits non-zero.
    """


async def _build_execution_engine():
    """Initialize ExecutionEngine with risk state synced from Gateway."""
    from orion.execution.execution_engine import ExecutionEngine

    engine = ExecutionEngine()
    await engine.initialize()
    return engine


async def _lease_is_ours(run_id: str) -> bool:
    """Return True iff the lease row still records ``run_id`` as owner.

    ``renew_service_lease`` deliberately swallows a stolen lease (logs
    ``service_lease_lost`` and returns) so a shared, non-fatal heartbeat
    can't crash benign callers. The position monitor needs the opposite:
    a stolen lease is fatal. We re-read the row ourselves rather than
    change the shared renewal helper's contract.
    """
    from sqlalchemy import select

    from orion.core.service_lease import _service_lease_key
    from orion.storage.db import async_session_factory
    from orion.storage.models import SystemStatus

    key = _service_lease_key(_LEASE_SERVICE_ID)
    async with async_session_factory() as session:
        existing = (await session.execute(select(SystemStatus).where(SystemStatus.key == key))).scalars().first()
        if existing is None:
            # Row vanished: treat as ours (next renew re-creates it).
            return True
        return f"run_id={run_id}" in (existing.details or "")


async def _renew_lease_forever(run_id: str) -> None:
    """Heartbeat the single-instance lease so a competing daemon stays out.

    Renewal swallows its own transient DB errors (see
    ``renew_service_lease``); a blip just retries on the next tick. But a
    lease that has been stolen by another owner is FATAL here: we raise
    ``LeaseLostError`` so the caller can hard-stop the monitor loop before
    any further close can be submitted. Runs until cancelled when the
    daemon loop exits, or until the lease is lost.
    """
    while True:
        await asyncio.sleep(_LEASE_RENEW_INTERVAL_SECONDS)
        await renew_service_lease(_LEASE_SERVICE_ID, run_id)
        if not await _lease_is_ours(run_id):
            logger.critical(
                "position_monitor_lease_lost",
                extra={"event_type": "POSITION_MONITOR_LEASE_LOST", "run_id": run_id},
            )
            raise LeaseLostError(
                f"position_monitor lease (run_id={run_id}) was taken by another owner; "
                "stopping to preserve the single-close-executor invariant."
            )


async def _run_with_lease_guard(run_id: str, work: asyncio.Future) -> None:
    """Run ``work`` alongside the lease heartbeat; lease loss aborts ``work``.

    Starts the renewal task IMMEDIATELY (before any heavy init the caller
    folds into ``work``) and races the two: if the renewal task raises
    ``LeaseLostError`` first, ``work`` is cancelled so no close can be
    submitted after the lease is known lost, and the error propagates.
    """
    renew_task = asyncio.create_task(_renew_lease_forever(run_id))
    work_task = asyncio.ensure_future(work)
    try:
        done, _pending = await asyncio.wait(
            {renew_task, work_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if renew_task in done:
            # Renewal finished first — only happens on lease loss (it never
            # returns normally). Cancel work, then surface the fatal error.
            work_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await work_task
            renew_task.result()  # re-raises LeaseLostError
        else:
            # Work finished (clean or crashing); stop the heartbeat.
            renew_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renew_task
            work_task.result()  # re-raises any work error
    finally:
        for task in (renew_task, work_task):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


async def main() -> None:
    parser = argparse.ArgumentParser(description="Orion Position Monitor")
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Seconds between position checks (default: 60)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log exit signals but don't execute",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one check and exit (for testing)",
    )

    args = parser.parse_args()

    logger.info(
        f"Position Monitor starting (interval={args.interval}s, dry_run={args.dry_run}, stage={system_settings.orion_stage})",
        extra={"event": "monitor_cli_start", "stage": system_settings.orion_stage},
    )

    # Single-instance lease decision (Wave C RB.4, plan-review round 1).
    #
    # Only `--dry-run` is genuinely read-only: run_check() passes dry_run
    # through to execute_exits(), which short-circuits before any order
    # submission, AND skips _reprotect_unprotected_positions (the only other
    # order-submitting path). `--once` ALONE is NOT read-only — a bare
    # `--once` runs run_check(dry_run=False), which can submit live closes.
    #
    # So the lease is skipped iff dry_run is set. This lets the W6 deploy
    # parity gate (`--dry-run --once`) run while the docker copy still holds
    # the lease without a relaunch-thrash, while any execute-capable mode
    # (the live daemon, OR a bare `--once`) acquires the lease and fails
    # loudly on a competing fresh lease.
    lease_run_id: str | None = None
    if not args.dry_run:
        await init_db()
        lease_run_id = await acquire_service_lease(_LEASE_SERVICE_ID)

    async def _do_work() -> None:
        # Heavy init lives INSIDE the lease-guarded work so the renewal task
        # (started first by _run_with_lease_guard) covers a slow init: if the
        # lease is stolen while ExecutionEngine is initializing, the guard
        # aborts before any check runs.
        gateway_client = get_gateway_trading_client()
        execution_engine = await _build_execution_engine()

        if args.once:
            # Single execute-capable check via GatewayTradingClient.
            adapter = GatewayPositionAdapter(gateway_client)
            await adapter.refresh()
            monitor = get_position_monitor(execution_engine=execution_engine)
            summary = await monitor.run_check(adapter, dry_run=args.dry_run)
            logger.info(
                "Single check complete",
                extra={"event": "monitor_single_check_done", **summary},
            )
        else:
            await run_position_monitor_loop(
                check_interval_seconds=args.interval,
                dry_run=args.dry_run,
                execution_engine=execution_engine,
                gateway_client=gateway_client,
            )

    if lease_run_id is not None:
        # Execute-capable mode (live daemon OR bare --once): start renewal
        # IMMEDIATELY and make lease loss fatal. Covers both the slow init
        # window and the (possibly unbounded) run.
        await _run_with_lease_guard(lease_run_id, _do_work())
    else:
        # --dry-run: no lease, read-only, cannot execute.
        await _do_work()


if __name__ == "__main__":
    run_entrypoint("orion.main_position_monitor", main())
