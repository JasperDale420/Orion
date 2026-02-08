from datetime import datetime, timedelta, timezone

from orion.jobs.quality_guardrails import _env_int, _next_last_run, _should_run


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
