"""Tests for the market-open data-flow check.

Background: on 2026-06-08 a service-lease split-brain starved ingestion and
only 403 bronze events landed all day (normal is ~150k-200k). The split-brain
was fixed by hand hours later. This check makes that class of silent stall
loud by paging at the open if the bronze feed is not fresh during market
hours.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from orion.jobs.market_open_dataflow_check import (
    DEFAULT_MAX_AGE_SECONDS,
    DataflowResult,
    Severity,
    evaluate,
    is_market_open,
    main,
    parse_bronze_max_ts,
    run_check,
)

# A weekday (Monday 2026-06-08) 10:00 ET == 14:00 UTC — inside the cash session.
MID_SESSION_UTC = datetime(2026, 6, 8, 14, 0, tzinfo=UTC)


# ---- is_market_open ---------------------------------------------------------


@pytest.mark.unit
def test_market_open_during_session() -> None:
    # Monday 09:40 ET == 13:40 UTC
    assert is_market_open(datetime(2026, 6, 8, 13, 40, tzinfo=UTC)) is True


@pytest.mark.unit
def test_market_closed_before_open() -> None:
    # Monday 09:00 ET == 13:00 UTC — before the 09:30 open
    assert is_market_open(datetime(2026, 6, 8, 13, 0, tzinfo=UTC)) is False


@pytest.mark.unit
def test_market_closed_after_close() -> None:
    # Monday 16:30 ET == 20:30 UTC — after the 16:00 close
    assert is_market_open(datetime(2026, 6, 8, 20, 30, tzinfo=UTC)) is False


@pytest.mark.unit
def test_market_closed_on_weekend() -> None:
    # Saturday 2026-06-13 14:00 UTC (would be mid-session on a weekday)
    assert is_market_open(datetime(2026, 6, 13, 14, 0, tzinfo=UTC)) is False


@pytest.mark.unit
def test_market_close_boundary_is_exclusive() -> None:
    # Exactly 16:00 ET == 20:00 UTC is closed (session is [09:30, 16:00))
    assert is_market_open(datetime(2026, 6, 8, 20, 0, tzinfo=UTC)) is False
    # 09:30 ET == 13:30 UTC is open (inclusive lower bound)
    assert is_market_open(datetime(2026, 6, 8, 13, 30, tzinfo=UTC)) is True


@pytest.mark.unit
def test_market_open_requires_tz_aware() -> None:
    with pytest.raises(ValueError):
        is_market_open(datetime(2026, 6, 8, 14, 0))  # naive


# ---- parse_bronze_max_ts ----------------------------------------------------


@pytest.mark.unit
def test_parse_psql_timestamptz() -> None:
    parsed = parse_bronze_max_ts("2026-06-08 18:23:36.934345+00")
    assert parsed == datetime(2026, 6, 8, 18, 23, 36, 934345, tzinfo=UTC)


@pytest.mark.unit
def test_parse_empty_table_returns_none() -> None:
    assert parse_bronze_max_ts("") is None
    assert parse_bronze_max_ts("   \n") is None


@pytest.mark.unit
def test_parse_non_utc_offset_normalised_to_utc() -> None:
    # -04 offset (ET summer) should normalise to the same UTC instant
    parsed = parse_bronze_max_ts("2026-06-08 14:00:00-04")
    assert parsed == datetime(2026, 6, 8, 18, 0, tzinfo=UTC)


@pytest.mark.unit
def test_parse_garbage_returns_none() -> None:
    assert parse_bronze_max_ts("not-a-timestamp") is None


# ---- evaluate (the core decision) -------------------------------------------


@pytest.mark.unit
def test_fresh_bronze_during_session_is_ok() -> None:
    fresh = MID_SESSION_UTC - timedelta(seconds=30)
    result = evaluate(MID_SESSION_UTC, fresh, gateway_up=True)
    assert result.severity is Severity.OK
    assert result.alert is False
    assert result.bronze_fresh is True


@pytest.mark.unit
def test_stale_bronze_during_session_is_critical() -> None:
    stale = MID_SESSION_UTC - timedelta(seconds=DEFAULT_MAX_AGE_SECONDS + 60)
    result = evaluate(MID_SESSION_UTC, stale, gateway_up=True)
    assert result.severity is Severity.CRITICAL
    assert result.alert is True
    assert result.bronze_fresh is False
    assert "STALLED" in result.message


@pytest.mark.unit
def test_stale_bronze_outside_session_no_alert() -> None:
    # 21:00 UTC == 17:00 ET, market closed; very stale data must NOT alert.
    after_close = datetime(2026, 6, 8, 21, 0, tzinfo=UTC)
    stale = after_close - timedelta(hours=3)
    result = evaluate(after_close, stale, gateway_up=True)
    assert result.severity is Severity.OK
    assert result.alert is False
    assert result.market_open is False
    assert "market closed" in result.message
    # diagnostics still populated
    assert result.bronze_age_seconds is not None


@pytest.mark.unit
def test_no_bronze_rows_during_session_is_critical() -> None:
    result = evaluate(MID_SESSION_UTC, None, gateway_up=True)
    assert result.severity is Severity.CRITICAL
    assert result.alert is True
    assert result.bronze_fresh is None
    assert "no bronze_events rows" in result.message


@pytest.mark.unit
def test_gateway_down_during_session_is_critical() -> None:
    fresh = MID_SESSION_UTC - timedelta(seconds=30)
    result = evaluate(MID_SESSION_UTC, fresh, gateway_up=False)
    assert result.severity is Severity.CRITICAL
    assert result.alert is True
    assert "unreachable" in result.message


@pytest.mark.unit
def test_gateway_down_outside_session_no_alert() -> None:
    after_close = datetime(2026, 6, 8, 21, 0, tzinfo=UTC)
    result = evaluate(after_close, after_close, gateway_up=False)
    assert result.alert is False
    assert result.market_open is False


@pytest.mark.unit
def test_age_exactly_at_threshold_is_ok() -> None:
    boundary = MID_SESSION_UTC - timedelta(seconds=DEFAULT_MAX_AGE_SECONDS)
    result = evaluate(MID_SESSION_UTC, boundary, gateway_up=True)
    # age == threshold is fresh (<=), so OK
    assert result.severity is Severity.OK
    assert result.alert is False


# ---- run_check (wiring: log row always written, notify only on alert) --------


@pytest.mark.unit
def test_run_check_writes_log_and_notifies_on_alert(tmp_path: Path) -> None:
    log_path = tmp_path / "dataflow.log"
    notified: list[DataflowResult] = []
    stale_iso = (MID_SESSION_UTC - timedelta(seconds=900)).strftime("%Y-%m-%d %H:%M:%S+00")

    result = run_check(
        bronze_runner=lambda: stale_iso,
        gateway_runner=lambda: True,
        notifier=notified.append,
        log_path=log_path,
        now_utc=MID_SESSION_UTC,
    )

    assert result.alert is True
    assert len(notified) == 1
    rows = log_path.read_text().strip().splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    assert row["severity"] == "CRITICAL"
    assert row["market_open"] is True


@pytest.mark.unit
def test_run_check_writes_log_but_no_notify_when_healthy(tmp_path: Path) -> None:
    log_path = tmp_path / "dataflow.log"
    notified: list[DataflowResult] = []
    fresh_iso = (MID_SESSION_UTC - timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S+00")

    result = run_check(
        bronze_runner=lambda: fresh_iso,
        gateway_runner=lambda: True,
        notifier=notified.append,
        log_path=log_path,
        now_utc=MID_SESSION_UTC,
    )

    assert result.alert is False
    assert notified == []
    # log row is still written even on a healthy pass (diagnostic value)
    assert len(log_path.read_text().strip().splitlines()) == 1


@pytest.mark.unit
def test_run_check_notifier_failure_does_not_propagate(tmp_path: Path) -> None:
    log_path = tmp_path / "dataflow.log"

    def boom(_: DataflowResult) -> None:
        raise RuntimeError("slack down")

    # A stale feed triggers an alert; the notifier raises but run_check must
    # still return the result and have written the durable log row.
    result = run_check(
        bronze_runner=lambda: "",  # no rows -> CRITICAL during session
        gateway_runner=lambda: True,
        notifier=boom,
        log_path=log_path,
        now_utc=MID_SESSION_UTC,
    )

    assert result.alert is True
    assert len(log_path.read_text().strip().splitlines()) == 1


@pytest.mark.unit
def test_main_returns_2_on_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    # main()'s only job beyond delegating is the exit-code contract: 2 on
    # alert, 0 otherwise. Patch run_check to return an alerting result.
    import orion.jobs.market_open_dataflow_check as mod

    alerting = DataflowResult(
        severity=Severity.CRITICAL,
        alert=True,
        market_open=True,
        bronze_fresh=False,
        bronze_age_seconds=900.0,
        bronze_max_ts="2026-06-08T13:45:00+00:00",
        gateway_up=True,
        message="bronze feed STALLED during market hours",
    )
    monkeypatch.setattr(mod, "run_check", lambda **_: alerting)
    assert main([]) == 2


@pytest.mark.unit
def test_main_returns_0_when_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    import orion.jobs.market_open_dataflow_check as mod

    healthy = DataflowResult(
        severity=Severity.OK,
        alert=False,
        market_open=False,
        bronze_fresh=True,
        bronze_age_seconds=12.0,
        bronze_max_ts="2026-06-08T18:23:36+00:00",
        gateway_up=True,
        message="market closed -- no alert",
    )
    monkeypatch.setattr(mod, "run_check", lambda **_: healthy)
    assert main([]) == 0


# ---- _discord_notifier ------------------------------------------------------


def _alerting_result() -> DataflowResult:
    return DataflowResult(
        severity=Severity.CRITICAL,
        alert=True,
        market_open=True,
        bronze_fresh=False,
        bronze_age_seconds=900.0,
        bronze_max_ts="2026-06-08T13:45:00+00:00",
        gateway_up=True,
        message="bronze feed STALLED during market hours",
    )


@pytest.mark.unit
def test_discord_notifier_noop_when_webhook_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unset webhook must NOT attempt a POST (the legacy bug was posting to an
    unset SLACK_WEBHOOK_URL, which paged no one — now it reads DISCORD_WEBHOOK_URL)."""
    import urllib.request

    from orion.jobs.market_open_dataflow_check import _discord_notifier

    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    called = False

    def _fail(*_a, **_k):
        nonlocal called
        called = True

    monkeypatch.setattr(urllib.request, "urlopen", _fail)
    _discord_notifier(_alerting_result())
    assert called is False


@pytest.mark.unit
def test_discord_notifier_posts_discord_content_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """When configured, posts the Discord webhook shape ({"content": ...}) — not
    Slack's {"text"/"attachments"}, which a Discord webhook rejects with 400."""
    import urllib.request

    from orion.jobs.market_open_dataflow_check import _discord_notifier

    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
    captured: dict[str, Any] = {}

    class _Resp:
        def read(self) -> bytes:
            return b""

    def _capture(req, timeout=None):  # noqa: ARG001
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["user_agent"] = req.headers.get("User-agent")
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", _capture)
    _discord_notifier(_alerting_result())

    assert captured["url"] == "https://discord.test/webhook"
    body = captured["body"]
    assert set(body.keys()) == {"content"}
    assert "bronze feed STALLED" in body["content"]
    assert "CRITICAL" in body["content"]
    assert captured["user_agent"] == "Orion-market-open-dataflow-check/1.0"
