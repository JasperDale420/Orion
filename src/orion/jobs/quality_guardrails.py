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
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Awaitable, Callable

from orion.core.logging_config import setup_logging
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


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str) -> set[str]:
    raw = os.getenv(name)
    if raw is None:
        return set()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _fail_fast_enabled_for_job(name: str) -> bool:
    if _env_flag("ORION_GUARDRAIL_FAIL_ON_CHECK_FAILURES", default=False):
        return True
    listed_jobs = _env_csv("ORION_GUARDRAIL_FAIL_ON_CHECK_FAILURES_JOBS")
    return name.strip().lower() in listed_jobs


def _should_run(last_run: datetime | None, interval_seconds: int, now: datetime) -> bool:
    if last_run is None:
        return True
    return (now - last_run).total_seconds() >= interval_seconds


def _next_last_run(last_run: datetime | None, succeeded: bool, now: datetime) -> datetime | None:
    if succeeded:
        return now
    return last_run


def _result_failure_summary(result: object) -> str | None:
    if not isinstance(result, dict):
        return None

    failed_raw = result.get("failed")
    if not isinstance(failed_raw, int):
        return None
    if failed_raw <= 0:
        return None

    issues = result.get("issues")
    issue_count = len(issues) if isinstance(issues, Sequence) and not isinstance(issues, (str, bytes)) else 0
    return f"failed_checks={failed_raw} issues={issue_count}"


async def _run_job(name: str, job: Callable[[], Awaitable[object]]) -> bool:
    fail_fast_on_check_failures = _fail_fast_enabled_for_job(name)

    result: object
    try:
        logger.info("Starting guardrail job: %s", name)
        result = await job()
    except Exception:
        logger.exception("Guardrail job failed: %s", name)
        return False

    failure_summary = _result_failure_summary(result)
    if failure_summary is not None:
        logger.error("Guardrail job reported failed checks: %s (%s)", name, failure_summary)
        if fail_fast_on_check_failures:
            raise RuntimeError(f"Guardrail check failures reported by {name}: {failure_summary}")
        return False

    logger.info("Completed guardrail job: %s", name)
    return True


async def run_guardrail_loop() -> None:
    await init_db()

    loop_sleep_seconds = _env_int("ORION_GUARDRAIL_LOOP_SECONDS", 60)
    reconcile_interval = _env_int("ORION_RECONCILE_INTERVAL_SECONDS", 3600)
    quality_interval = _env_int("ORION_QUALITY_CHECK_INTERVAL_SECONDS", 1800)
    feature_validate_interval = _env_int("ORION_FEATURE_VALIDATE_INTERVAL_SECONDS", 3600)
    reconcile_lookback_days = _env_int("ORION_RECONCILE_LOOKBACK_DAYS", 7)

    logger.info(
        "Quality guardrails scheduler started: " "reconcile=%ss quality=%ss validate=%ss sleep=%ss lookback_days=%s",
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
            reconcile_ok = await _run_job(
                "reconciliation",
                lambda: run_reconciliation(lookback_days=reconcile_lookback_days),
            )
            last_reconcile = _next_last_run(last_reconcile, succeeded=reconcile_ok, now=now)

        if _should_run(last_quality, quality_interval, now):
            quality_ok = await _run_job("data_quality_checker", run_quality_checks)
            last_quality = _next_last_run(last_quality, succeeded=quality_ok, now=now)

        if _should_run(last_validate, feature_validate_interval, now):
            validate_ok = await _run_job("feature_sanity_validation", run_sanity_checks)
            last_validate = _next_last_run(last_validate, succeeded=validate_ok, now=now)

        await asyncio.sleep(loop_sleep_seconds)


if __name__ == "__main__":
    setup_logging()
    asyncio.run(run_guardrail_loop())
