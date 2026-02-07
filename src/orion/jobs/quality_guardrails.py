"""
Quality Guardrails Scheduler.

Runs migration-critical guardrail jobs on fixed intervals:
- Data reconciliation (bronze vs silver parity)
- Data quality checks
- Feature sanity validation
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Awaitable, Callable

from orion.jobs.data_quality_checker import run_quality_checks
from orion.jobs.reconcile_backfill import run_reconciliation
from orion.jobs.validate_features import run_sanity_checks
from orion.storage.db import init_db

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid integer env for %s=%s; using default=%s", name, raw, default)
        return default
    return max(1, value)


def _should_run(last_run: datetime | None, interval_seconds: int, now: datetime) -> bool:
    if last_run is None:
        return True
    return (now - last_run).total_seconds() >= interval_seconds


async def _run_job(name: str, job: Callable[[], Awaitable[object]]) -> None:
    try:
        logger.info("Starting guardrail job: %s", name)
        await job()
        logger.info("Completed guardrail job: %s", name)
    except Exception:
        logger.exception("Guardrail job failed: %s", name)


async def run_guardrail_loop() -> None:
    await init_db()

    loop_sleep_seconds = _env_int("ORION_GUARDRAIL_LOOP_SECONDS", 60)
    reconcile_interval = _env_int("ORION_RECONCILE_INTERVAL_SECONDS", 3600)
    quality_interval = _env_int("ORION_QUALITY_CHECK_INTERVAL_SECONDS", 1800)
    feature_validate_interval = _env_int("ORION_FEATURE_VALIDATE_INTERVAL_SECONDS", 3600)
    reconcile_lookback_days = _env_int("ORION_RECONCILE_LOOKBACK_DAYS", 7)

    logger.info(
        "Quality guardrails scheduler started: "
        "reconcile=%ss quality=%ss validate=%ss sleep=%ss lookback_days=%s",
        reconcile_interval,
        quality_interval,
        feature_validate_interval,
        loop_sleep_seconds,
        reconcile_lookback_days,
    )

    last_reconcile: datetime | None = None
    last_quality: datetime | None = None
    last_validate: datetime | None = None

    while True:
        now = datetime.now(timezone.utc)

        if _should_run(last_reconcile, reconcile_interval, now):
            await _run_job(
                "reconciliation",
                lambda: run_reconciliation(lookback_days=reconcile_lookback_days),
            )
            last_reconcile = now

        if _should_run(last_quality, quality_interval, now):
            await _run_job("data_quality_checker", run_quality_checks)
            last_quality = now

        if _should_run(last_validate, feature_validate_interval, now):
            await _run_job("feature_sanity_validation", run_sanity_checks)
            last_validate = now

        await asyncio.sleep(loop_sleep_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    asyncio.run(run_guardrail_loop())
