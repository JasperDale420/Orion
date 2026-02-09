from datetime import datetime, timedelta, timezone

import pytest

from orion.jobs.quality_guardrails import (
    _env_int,
    _failure_backoff_elapsed,
    _job_failure_backoff_seconds,
    _next_last_run,
    _resolve_job_failure_backoff_policy,
    _resolve_job_failure_backoff_policy_cached,
    _resolve_runtime_backoff_policy_cached,
    _runtime_backoff_policy_from_value,
    _should_run,
)


def test_env_int_uses_default_for_invalid(monkeypatch) -> None:
    monkeypatch.setenv("ORION_TEST_INT", "not-an-int")
    assert _env_int("ORION_TEST_INT", 42) == 42


def test_env_int_clamps_to_minimum_one(monkeypatch) -> None:
    monkeypatch.setenv("ORION_TEST_INT", "0")
    assert _env_int("ORION_TEST_INT", 7) == 1


def test_should_run_true_when_never_ran() -> None:
    now = datetime.now(timezone.utc)
    assert _should_run(None, 60, now) is True


def test_should_run_respects_interval() -> None:
    now = datetime.now(timezone.utc)
    last_run = now - timedelta(seconds=30)
    assert _should_run(last_run, 60, now) is False
    assert _should_run(last_run, 20, now) is True


def test_next_last_run_updates_timestamp_only_on_success() -> None:
    now = datetime.now(timezone.utc)
    prev = now - timedelta(minutes=5)
    assert _next_last_run(prev, succeeded=True, now=now) == now
    assert _next_last_run(prev, succeeded=False, now=now) == prev


def test_failure_backoff_elapsed_true_without_failure_timestamp() -> None:
    now = datetime.now(timezone.utc)
    assert _failure_backoff_elapsed(last_failure=None, backoff_seconds=120, now=now) is True


def test_failure_backoff_elapsed_respects_backoff_window() -> None:
    now = datetime.now(timezone.utc)
    last_failure = now - timedelta(seconds=30)
    assert _failure_backoff_elapsed(last_failure=last_failure, backoff_seconds=60, now=now) is False
    assert _failure_backoff_elapsed(last_failure=last_failure, backoff_seconds=20, now=now) is True


def test_job_failure_backoff_seconds_uses_global_default_when_not_configured(monkeypatch) -> None:
    monkeypatch.delenv("ORION_GUARDRAIL_FAILURE_BACKOFF_SECONDS_JOBS", raising=False)
    assert _job_failure_backoff_seconds("feature_sanity_validation", default_seconds=45) == 45


def test_job_failure_backoff_seconds_uses_job_specific_override(monkeypatch) -> None:
    monkeypatch.setenv(
        "ORION_GUARDRAIL_FAILURE_BACKOFF_SECONDS_JOBS",
        "reconciliation=15, feature_sanity_validation=120",
    )
    assert _job_failure_backoff_seconds("feature_sanity_validation", default_seconds=45) == 120
    assert _job_failure_backoff_seconds("data_quality_checker", default_seconds=45) == 45


def test_resolve_job_failure_backoff_policy_uses_global_default(monkeypatch) -> None:
    monkeypatch.delenv("ORION_GUARDRAIL_FAILURE_BACKOFF_SECONDS_JOBS", raising=False)
    policy = _resolve_job_failure_backoff_policy(default_seconds=33)
    assert policy == {
        "reconciliation": 33,
        "data_quality_checker": 33,
        "feature_sanity_validation": 33,
    }


def test_resolve_job_failure_backoff_policy_reloads_env_each_call(monkeypatch) -> None:
    monkeypatch.setenv("ORION_GUARDRAIL_FAILURE_BACKOFF_SECONDS_JOBS", "reconciliation=10")
    first = _resolve_job_failure_backoff_policy(default_seconds=30)

    monkeypatch.setenv("ORION_GUARDRAIL_FAILURE_BACKOFF_SECONDS_JOBS", "reconciliation=90")
    second = _resolve_job_failure_backoff_policy(default_seconds=30)

    assert first["reconciliation"] == 10
    assert second["reconciliation"] == 90


def test_resolve_job_failure_backoff_policy_cached_reuses_policy_when_env_unchanged(monkeypatch) -> None:
    monkeypatch.setenv("ORION_GUARDRAIL_FAILURE_BACKOFF_SECONDS_JOBS", "reconciliation=10")
    raw1, policy1 = _resolve_job_failure_backoff_policy_cached(
        default_seconds=30,
        cached_raw=None,
        cached_policy=None,
    )
    raw2, policy2 = _resolve_job_failure_backoff_policy_cached(
        default_seconds=30,
        cached_raw=raw1,
        cached_policy=policy1,
    )

    assert raw1 == raw2
    assert policy2 is policy1


def test_resolve_job_failure_backoff_policy_cached_rebuilds_on_env_change(monkeypatch) -> None:
    monkeypatch.setenv("ORION_GUARDRAIL_FAILURE_BACKOFF_SECONDS_JOBS", "reconciliation=10")
    raw1, policy1 = _resolve_job_failure_backoff_policy_cached(
        default_seconds=30,
        cached_raw=None,
        cached_policy=None,
    )

    monkeypatch.setenv("ORION_GUARDRAIL_FAILURE_BACKOFF_SECONDS_JOBS", "reconciliation=90")
    raw2, policy2 = _resolve_job_failure_backoff_policy_cached(
        default_seconds=30,
        cached_raw=raw1,
        cached_policy=policy1,
    )

    assert raw1 != raw2
    assert policy2 is not policy1
    assert policy2["reconciliation"] == 90


def test_runtime_backoff_policy_from_value_parses_and_clamps() -> None:
    policy = _runtime_backoff_policy_from_value(
        {
            "reconciliation": "15",
            "data_quality_checker": -5,
            "feature_sanity_validation": 90,
            "unknown_job": 999,
        },
        default_seconds=30,
    )
    assert policy == {
        "reconciliation": 15,
        "data_quality_checker": 0,
        "feature_sanity_validation": 90,
    }


def test_runtime_backoff_policy_from_value_returns_none_for_unusable_payload() -> None:
    assert _runtime_backoff_policy_from_value("invalid", default_seconds=30) is None
    assert _runtime_backoff_policy_from_value({"unknown_job": 1}, default_seconds=30) is None


@pytest.mark.asyncio
async def test_resolve_runtime_backoff_policy_cached_reuses_when_updated_ts_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updated = datetime(2026, 2, 9, 16, 0, tzinfo=timezone.utc)
    calls: list[int] = []

    async def _fake_load_runtime_backoff_config_row() -> tuple[datetime, object] | None:
        calls.append(1)
        return updated, {"reconciliation": 12}

    monkeypatch.setattr(
        "orion.jobs.quality_guardrails._load_runtime_backoff_config_row",
        _fake_load_runtime_backoff_config_row,
    )

    updated_1, policy_1 = await _resolve_runtime_backoff_policy_cached(
        default_seconds=30,
        cached_updated_ts=None,
        cached_policy=None,
    )
    updated_2, policy_2 = await _resolve_runtime_backoff_policy_cached(
        default_seconds=30,
        cached_updated_ts=updated_1,
        cached_policy=policy_1,
    )

    assert len(calls) == 2
    assert updated_1 == updated_2 == updated
    assert policy_2 is policy_1
