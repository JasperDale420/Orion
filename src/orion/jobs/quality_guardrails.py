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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orion.core.logging_config import setup_logging
from orion.jobs.data_quality_checker import run_quality_checks
from orion.jobs.reconcile_backfill import run_reconciliation
from orion.jobs.validate_features import run_sanity_checks
from orion.shared.db_utils import db_query
from orion.shared.utils import ensure_utc
from orion.storage.db import init_db
from orion.storage.models import RuntimeConfig

logger = logging.getLogger(__name__)
JOB_BACKOFF_ENV = "ORION_GUARDRAIL_FAILURE_BACKOFF_SECONDS_JOBS"
RUNTIME_BACKOFF_CONFIG_KEY = "quality_guardrails.backoff_seconds_jobs"


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


def _env_nonneg_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid integer env for %s=%s; using default=%s", name, raw, default)
        return default
    return max(0, value)


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


def _parse_job_nonneg_int_map(raw: str | None, env_name: str) -> dict[str, int]:
    if raw is None:
        return {}

    parsed: dict[str, int] = {}
    for pair in raw.split(","):
        item = pair.strip()
        if not item:
            continue
        if "=" not in item:
            logger.warning("Invalid key/value pair in %s=%s; expected job=seconds", env_name, item)
            continue

        job_raw, seconds_raw = item.split("=", 1)
        job_name = job_raw.strip().lower()
        if not job_name:
            logger.warning("Invalid key/value pair in %s=%s; missing job name", env_name, item)
            continue

        try:
            seconds = int(seconds_raw.strip())
        except ValueError:
            logger.warning("Invalid backoff seconds in %s=%s; skipping", env_name, item)
            continue

        parsed[job_name] = max(0, seconds)
    return parsed


def _env_job_nonneg_int_map(name: str) -> dict[str, int]:
    return _parse_job_nonneg_int_map(os.getenv(name), env_name=name)


def _fail_fast_enabled_for_job(name: str) -> bool:
    if _env_flag("ORION_GUARDRAIL_FAIL_ON_CHECK_FAILURES", default=False):
        return True
    listed_jobs = _env_csv("ORION_GUARDRAIL_FAIL_ON_CHECK_FAILURES_JOBS")
    return name.strip().lower() in listed_jobs


def _job_failure_backoff_seconds(name: str, default_seconds: int) -> int:
    configured = _env_job_nonneg_int_map(JOB_BACKOFF_ENV)
    return configured.get(name.strip().lower(), default_seconds)


def _resolve_job_failure_backoff_policy(default_seconds: int) -> dict[str, int]:
    configured = _env_job_nonneg_int_map(JOB_BACKOFF_ENV)
    return {
        "reconciliation": configured.get("reconciliation", default_seconds),
        "data_quality_checker": configured.get("data_quality_checker", default_seconds),
        "feature_sanity_validation": configured.get("feature_sanity_validation", default_seconds),
    }


def _resolve_job_failure_backoff_policy_cached(
    default_seconds: int,
    cached_raw: str | None,
    cached_policy: dict[str, int] | None,
) -> tuple[str | None, dict[str, int]]:
    current_raw = os.getenv(JOB_BACKOFF_ENV)
    if cached_policy is not None and current_raw == cached_raw:
        return cached_raw, cached_policy

    configured = _parse_job_nonneg_int_map(current_raw, env_name=JOB_BACKOFF_ENV)
    policy = {
        "reconciliation": configured.get("reconciliation", default_seconds),
        "data_quality_checker": configured.get("data_quality_checker", default_seconds),
        "feature_sanity_validation": configured.get("feature_sanity_validation", default_seconds),
    }
    return current_raw, policy


def _runtime_backoff_policy_from_value(raw_value: object, default_seconds: int) -> dict[str, int] | None:
    if not isinstance(raw_value, dict):
        return None

    policy = {
        "reconciliation": default_seconds,
        "data_quality_checker": default_seconds,
        "feature_sanity_validation": default_seconds,
    }

    saw_valid_override = False
    for raw_key, raw_seconds in raw_value.items():
        if not isinstance(raw_key, str):
            continue
        key = raw_key.strip().lower()
        if key not in policy:
            continue
        try:
            seconds = int(raw_seconds)
        except (TypeError, ValueError):
            continue
        policy[key] = max(0, seconds)
        saw_valid_override = True

    if not saw_valid_override:
        return None
    return policy


async def _load_runtime_backoff_config_row() -> tuple[datetime | None, object] | None:
    async def query(session: AsyncSession) -> tuple[datetime | None, object] | None:
        stmt = select(RuntimeConfig).where(RuntimeConfig.key == RUNTIME_BACKOFF_CONFIG_KEY)
        result = await session.execute(stmt)
        row = result.scalars().first()
        if row is None:
            return None
        return ensure_utc(row.updated_ts_utc), row.value_json

    try:
        return await db_query(query)
    except Exception:
        logger.exception("Failed loading runtime backoff config row; falling back to env policy")
        return None


async def _resolve_runtime_backoff_policy_cached(
    default_seconds: int,
    cached_updated_ts: datetime | None,
    cached_policy: dict[str, int] | None,
) -> tuple[datetime | None, dict[str, int] | None]:
    loaded = await _load_runtime_backoff_config_row()
    if loaded is None:
        return None, None

    updated_ts, raw_value = loaded
    if updated_ts == cached_updated_ts:
        return cached_updated_ts, cached_policy

    return updated_ts, _runtime_backoff_policy_from_value(raw_value, default_seconds)


def _should_run(last_run: datetime | None, interval_seconds: int, now: datetime) -> bool:
    if last_run is None:
        return True
    return (now - last_run).total_seconds() >= interval_seconds


def _next_last_run(last_run: datetime | None, succeeded: bool, now: datetime) -> datetime | None:
    if succeeded:
        return now
    return last_run


def _failure_backoff_elapsed(last_failure: datetime | None, backoff_seconds: int, now: datetime) -> bool:
    if last_failure is None or backoff_seconds <= 0:
        return True
    return (now - last_failure).total_seconds() >= backoff_seconds


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
    failure_backoff_seconds = _env_nonneg_int("ORION_GUARDRAIL_FAILURE_BACKOFF_SECONDS", 0)

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
    last_reconcile_failure: datetime | None = None
    last_quality_failure: datetime | None = None
    last_validate_failure: datetime | None = None
    env_backoff_policy_raw: str | None = None
    env_backoff_policy: dict[str, int] | None = None
    runtime_backoff_updated_ts: datetime | None = None
    runtime_backoff_policy: dict[str, int] | None = None

    while True:
        now = datetime.now(timezone.utc)
        env_backoff_policy_raw, env_backoff_policy = _resolve_job_failure_backoff_policy_cached(
            default_seconds=failure_backoff_seconds,
            cached_raw=env_backoff_policy_raw,
            cached_policy=env_backoff_policy,
        )
        runtime_backoff_updated_ts, runtime_backoff_policy = await _resolve_runtime_backoff_policy_cached(
            default_seconds=failure_backoff_seconds,
            cached_updated_ts=runtime_backoff_updated_ts,
            cached_policy=runtime_backoff_policy,
        )
        effective_backoff_policy = runtime_backoff_policy if runtime_backoff_policy is not None else env_backoff_policy

        if _should_run(last_reconcile, reconcile_interval, now) and _failure_backoff_elapsed(
            last_reconcile_failure,
            effective_backoff_policy["reconciliation"],
            now,
        ):
            reconcile_ok = await _run_job(
                "reconciliation",
                lambda: run_reconciliation(lookback_days=reconcile_lookback_days),
            )
            last_reconcile = _next_last_run(last_reconcile, succeeded=reconcile_ok, now=now)
            if reconcile_ok:
                last_reconcile_failure = None
            else:
                last_reconcile_failure = now

        if _should_run(last_quality, quality_interval, now) and _failure_backoff_elapsed(
            last_quality_failure,
            effective_backoff_policy["data_quality_checker"],
            now,
        ):
            quality_ok = await _run_job("data_quality_checker", run_quality_checks)
            last_quality = _next_last_run(last_quality, succeeded=quality_ok, now=now)
            if quality_ok:
                last_quality_failure = None
            else:
                last_quality_failure = now

        if _should_run(last_validate, feature_validate_interval, now) and _failure_backoff_elapsed(
            last_validate_failure,
            effective_backoff_policy["feature_sanity_validation"],
            now,
        ):
            validate_ok = await _run_job("feature_sanity_validation", run_sanity_checks)
            last_validate = _next_last_run(last_validate, succeeded=validate_ok, now=now)
            if validate_ok:
                last_validate_failure = None
            else:
                last_validate_failure = now

        await asyncio.sleep(loop_sleep_seconds)


if __name__ == "__main__":
    setup_logging()
    asyncio.run(run_guardrail_loop())
