from __future__ import annotations

import logging

import pytest

from orion.jobs import quality_guardrails


def test_result_failure_summary_ignores_non_dict() -> None:
    assert quality_guardrails._result_failure_summary(None) is None
    assert quality_guardrails._result_failure_summary("ok") is None


def test_result_failure_summary_ignores_zero_failed() -> None:
    assert quality_guardrails._result_failure_summary({"failed": 0, "issues": []}) is None


def test_result_failure_summary_reports_failed_count_and_issue_count() -> None:
    summary = quality_guardrails._result_failure_summary(
        {"failed": 2, "issues": ["a", "b", "c"]},
    )
    assert summary == "failed_checks=2 issues=3"


@pytest.mark.asyncio
async def test_run_job_logs_error_when_result_contains_failures(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)

    async def _job():
        return {"failed": 1, "issues": ["x"]}

    success = await quality_guardrails._run_job("feature_sanity_validation", _job)
    assert success is False
    assert "Guardrail job reported failed checks" in caplog.text


@pytest.mark.asyncio
async def test_run_job_logs_completed_for_non_failure_result(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)

    async def _job():
        return {"failed": 0, "issues": []}

    success = await quality_guardrails._run_job("feature_sanity_validation", _job)
    assert success is True
    assert "Completed guardrail job" in caplog.text


@pytest.mark.asyncio
async def test_run_job_raises_when_fail_fast_env_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_GUARDRAIL_FAIL_ON_CHECK_FAILURES", "1")

    async def _job():
        return {"failed": 2, "issues": ["x", "y"]}

    with pytest.raises(RuntimeError, match="feature_sanity_validation"):
        await quality_guardrails._run_job("feature_sanity_validation", _job)


@pytest.mark.asyncio
async def test_run_job_raises_when_job_is_listed_for_fail_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ORION_GUARDRAIL_FAIL_ON_CHECK_FAILURES", raising=False)
    monkeypatch.setenv(
        "ORION_GUARDRAIL_FAIL_ON_CHECK_FAILURES_JOBS",
        "reconciliation, feature_sanity_validation",
    )

    async def _job():
        return {"failed": 1, "issues": ["x"]}

    with pytest.raises(RuntimeError, match="feature_sanity_validation"):
        await quality_guardrails._run_job("feature_sanity_validation", _job)


@pytest.mark.asyncio
async def test_run_job_does_not_raise_when_job_not_listed_for_fail_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ORION_GUARDRAIL_FAIL_ON_CHECK_FAILURES", raising=False)
    monkeypatch.setenv("ORION_GUARDRAIL_FAIL_ON_CHECK_FAILURES_JOBS", "reconciliation")

    async def _job():
        return {"failed": 1, "issues": ["x"]}

    success = await quality_guardrails._run_job("feature_sanity_validation", _job)
    assert success is False
