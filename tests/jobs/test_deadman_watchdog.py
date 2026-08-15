"""Tests for the dead-man watchdog (orion.jobs.deadman_watchdog).

Covers the decision matrix:
  - fresh service => no alert
  - stale service (past its own budget) => alert
  - never-registered service => silent (no row, no alert)
  - stage freshness alerts ONLY during market hours
  - candidates-today is informational (never an alert)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def _isolated_deadman_state(tmp_path, monkeypatch):
    """Each test gets a fresh alert-suppression state file — the production
    default (logs/deadman_state.json) would leak suppression across tests and
    from real launchd runs."""
    monkeypatch.setenv("ORION_DEADMAN_STATE", str(tmp_path / "deadman_state.json"))


from sqlalchemy.ext.asyncio import async_sessionmaker

from orion.jobs import deadman_watchdog as dw
from orion.jobs.deadman_watchdog import (
    BRONZE_BUDGET_SECONDS,
    FUTURE_SKEW_SECONDS,
    evaluate_service_liveness,
    evaluate_stage_freshness,
    is_nyse_session_open,
    run_watchdog,
)
from orion.shared.liveness import publish_liveness
from orion.storage import db
from orion.storage.models import BronzeEvent
from orion.storage.models_liveness import ServiceLiveness
from orion.storage.models_silver import SilverSignal

# asyncio_mode = "auto" (pyproject) auto-detects async tests; no per-test mark
# needed. A global pytest.mark.asyncio would wrongly tag the sync pure-function
# tests below.

# A weekday during the cash session, and outside it, both in UTC.
MARKET_HOURS_UTC = datetime(2026, 6, 11, 15, 0, tzinfo=UTC)  # 11:00 ET Thu — open
AFTER_HOURS_UTC = datetime(2026, 6, 11, 2, 0, tzinfo=UTC)  # 22:00 ET prev day — closed


# ---- pure decision functions -------------------------------------------------


def _row(service: str, age_seconds: float, budget: int, *, now: datetime | None = None) -> ServiceLiveness:
    row_now = now or datetime.now(UTC)
    return ServiceLiveness(
        service=service,
        last_success_ts_utc=row_now - timedelta(seconds=age_seconds),
        cycle_count=5,
        last_error=None,
        cadence_budget_seconds=budget,
        updated_at=row_now,
    )


def test_fresh_service_no_alert():
    now = datetime.now(UTC)
    rows = [_row("ingestion", age_seconds=10, budget=300)]
    assert evaluate_service_liveness(rows, now) == []


def test_stale_service_alerts():
    now = datetime.now(UTC)
    rows = [_row("ingestion", age_seconds=900, budget=300)]
    alerts = evaluate_service_liveness(rows, now)
    assert len(alerts) == 1
    assert alerts[0].name == "ingestion"
    assert alerts[0].dedupe_key == "deadman_ingestion"


def test_each_service_judged_against_its_own_budget():
    now = datetime.now(UTC)
    rows = [
        _row("execution", age_seconds=400, budget=300),  # stale
        _row("reconcile_pnl", age_seconds=400, budget=86400 * 8),  # fresh (huge budget)
    ]
    alerts = evaluate_service_liveness(rows, now)
    assert [a.name for a in alerts] == ["execution"]


def test_market_bound_service_is_informational_when_market_closed():
    rows = [_row("ingestion", age_seconds=900, budget=300, now=AFTER_HOURS_UTC)]

    assert evaluate_service_liveness(rows, AFTER_HOURS_UTC, market_open=False) == []


def test_always_on_service_still_alerts_when_market_closed():
    rows = [_row("reconcile_pnl", age_seconds=900, budget=300, now=AFTER_HOURS_UTC)]

    alerts = evaluate_service_liveness(rows, AFTER_HOURS_UTC, market_open=False)

    assert [a.name for a in alerts] == ["reconcile_pnl"]


def test_retired_service_rows_are_ignored():
    rows = [
        _row("meta_search", age_seconds=900, budget=300, now=AFTER_HOURS_UTC),
        _row("meta_weekly", age_seconds=900, budget=300, now=AFTER_HOURS_UTC),
    ]

    assert evaluate_service_liveness(rows, AFTER_HOURS_UTC, market_open=False) == []


def test_reactivated_retired_service_resumes_normal_alerting():
    """A retired-service row is hidden only through its OWN retirement cutoff
    (2026-07-02T07:32:50Z, the deletion commit, for meta_search/meta_weekly),
    judged off updated_at. If a retired name is ever redeployed and publishes
    a fresh successful cycle, updated_at moves past the cutoff and the row is
    judged exactly like any other registered service — no watchdog roster
    edit is needed to un-hide a name that starts reporting again."""
    now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)  # long after the retirement cutoff
    rows = [_row("meta_search", age_seconds=900, budget=300, now=now)]

    alerts = evaluate_service_liveness(rows, now)

    assert [a.name for a in alerts] == ["meta_search"]


def test_reactivated_but_failing_retired_service_still_alerts():
    """A redeploy that fails before its first successful cycle must not stay
    hidden. publish_liveness bumps updated_at on an error-only publish without
    ever advancing last_success_ts_utc, so judging reactivation off
    last_success_ts_utc would leave an actively-crashing redeploy silently
    unmonitored forever — precisely the failure mode a dead-man watchdog
    exists to catch. This row's last_success_ts_utc is still the ancient
    pre-retirement value; only updated_at is recent, mirroring a real
    error-only republish."""
    now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    rows = [
        ServiceLiveness(
            service="meta_weekly",
            last_success_ts_utc=datetime(2026, 6, 1, tzinfo=UTC),  # never succeeded post-redeploy
            cycle_count=3,
            last_error="startup crash",
            cadence_budget_seconds=300,
            updated_at=now,  # error-only publish just happened
        )
    ]

    alerts = evaluate_service_liveness(rows, now)

    assert [a.name for a in alerts] == ["meta_weekly"]


def test_retired_service_row_predating_cutoff_still_ignored_even_when_fresh():
    """A row whose OWN updated_at is still on/before the retirement cutoff
    stays hidden even with a budget large enough to look "fresh" by age
    alone — a generous budget must never substitute for genuine post-cutoff
    activity."""
    now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    rows = [
        ServiceLiveness(
            service="meta_search",
            last_success_ts_utc=datetime(2026, 7, 1, tzinfo=UTC),
            cycle_count=3,
            last_error=None,
            cadence_budget_seconds=86400 * 365,  # huge budget => "fresh" by age alone
            updated_at=datetime(2026, 7, 1, tzinfo=UTC),  # last touched before retirement
        )
    ]

    assert evaluate_service_liveness(rows, now) == []


def test_retired_service_cutoff_boundary_exact_second_still_hidden():
    """A row updated at the exact retirement-commit instant is still
    considered pre-cutoff (the check is <=, not <) — the commit itself must
    never read as evidence of a reactivation."""
    cutoff = datetime(2026, 7, 2, 7, 32, 50, tzinfo=UTC)
    now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    rows = [
        ServiceLiveness(
            service="meta_search",
            last_success_ts_utc=cutoff,
            cycle_count=3,
            last_error=None,
            cadence_budget_seconds=300,
            updated_at=cutoff,
        )
    ]

    assert evaluate_service_liveness(rows, now) == []


def test_retired_service_cutoff_boundary_one_second_after_alerts():
    """A row updated one second after the retirement-commit instant is a
    genuine reactivation and is judged normally."""
    cutoff = datetime(2026, 7, 2, 7, 32, 50, tzinfo=UTC)
    just_after = cutoff + timedelta(seconds=1)
    now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    rows = [
        ServiceLiveness(
            service="meta_search",
            last_success_ts_utc=just_after,
            cycle_count=3,
            last_error=None,
            cadence_budget_seconds=300,
            updated_at=just_after,
        )
    ]

    assert [a.name for a in evaluate_service_liveness(rows, now)] == ["meta_search"]


def test_retired_service_row_with_future_dated_update_stays_hidden():
    """A retired-service row whose updated_at is corrupted/clock-skewed into
    the future must not be treated as reactivation evidence — trusting it
    would both wrongly resume paging on bad data AND (since a future
    last_success_ts_utc can never exceed its budget) permanently blind the
    very check reactivation is supposed to restore."""
    now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    rows = [
        ServiceLiveness(
            service="meta_search",
            last_success_ts_utc=now + timedelta(hours=1),
            cycle_count=3,
            last_error=None,
            cadence_budget_seconds=300,
            updated_at=now + timedelta(hours=1),  # far beyond FUTURE_SKEW_SECONDS
        )
    ]

    assert evaluate_service_liveness(rows, now) == []


def test_future_dated_service_success_alerts_as_data_quality_bug():
    """Any service (not just a retired one) with a last_success_ts_utc beyond
    FUTURE_SKEW_SECONDS in the future is a clock/data-quality bug and must
    alert explicitly — a negative age can never exceed a budget, so without
    this guard a corrupted row would look permanently, silently healthy."""
    now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    rows = [_row("ingestion", age_seconds=-3600, budget=300, now=now)]  # 1h in the future

    alerts = evaluate_service_liveness(rows, now)

    assert len(alerts) == 1
    assert alerts[0].name == "ingestion"
    assert "FUTURE-DATED" in alerts[0].message


def test_reactivated_retired_service_with_future_dated_success_alerts():
    """A row that un-hides (updated_at past the retirement cutoff — e.g. a
    genuine current error publish) but whose last_success_ts_utc is separately
    corrupted into the future must not go silent. Reactivation alone is not
    enough: the ordinary staleness check would compute a negative age from
    the bad last_success_ts_utc and never alert, so this is caught as its own
    clock/data-quality finding instead."""
    now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    rows = [
        ServiceLiveness(
            service="meta_search",
            last_success_ts_utc=now + timedelta(hours=1),  # corrupted, stale bad write
            cycle_count=3,
            last_error="startup crash",
            cadence_budget_seconds=300,
            updated_at=now,  # genuine current error publish — past the cutoff
        )
    ]

    alerts = evaluate_service_liveness(rows, now)

    assert len(alerts) == 1
    assert alerts[0].name == "meta_search"
    assert "FUTURE-DATED" in alerts[0].message


def test_stage_fresh_no_alert():
    now = datetime.now(UTC)
    max_ts = now - timedelta(seconds=60)
    assert evaluate_stage_freshness("bronze", max_ts, BRONZE_BUDGET_SECONDS, now) is None


def test_stage_stale_alerts():
    now = datetime.now(UTC)
    max_ts = now - timedelta(seconds=BRONZE_BUDGET_SECONDS + 60)
    alert = evaluate_stage_freshness("bronze", max_ts, BRONZE_BUDGET_SECONDS, now)
    assert alert is not None
    assert alert.name == "bronze"
    assert "STALE" in alert.message


def test_stage_no_rows_alerts():
    now = datetime.now(UTC)
    alert = evaluate_stage_freshness("silver", None, 600, now)
    assert alert is not None
    assert "NO rows" in alert.message


def test_stage_future_timestamp_alerts():
    """A max_ts well beyond now+skew is a clock/data-quality bug: a negative age
    can never exceed the budget, so without this guard the stage would NEVER
    alert. The watchdog must surface the future-dated rows instead of going
    blind (the 2026-07-11 bronze_max smoke-test residue)."""
    now = datetime.now(UTC)
    max_ts = now + timedelta(seconds=FUTURE_SKEW_SECONDS + 3600)
    alert = evaluate_stage_freshness("bronze", max_ts, BRONZE_BUDGET_SECONDS, now)
    assert alert is not None
    assert alert.name == "bronze"
    assert "FUTURE" in alert.message
    assert alert.age_seconds is not None
    assert alert.age_seconds < 0
    # A future-dated alert must NOT share the suppression key of an ordinary
    # stale alert for the same stage, or one would mask the other for 15 min.
    assert alert.dedupe_key == "deadman_bronze_future_ts"


def test_stage_minor_future_skew_does_not_alert():
    """A timestamp slightly in the future but within FUTURE_SKEW tolerance is
    benign clock skew, not a data-quality bug — no alert."""
    now = datetime.now(UTC)
    max_ts = now + timedelta(seconds=FUTURE_SKEW_SECONDS - 10)
    assert evaluate_stage_freshness("bronze", max_ts, BRONZE_BUDGET_SECONDS, now) is None


def test_stage_exact_future_skew_boundary_does_not_alert():
    """Exactly now+skew is tolerated (strict > comparison): boundary is benign."""
    now = datetime.now(UTC)
    max_ts = now + timedelta(seconds=FUTURE_SKEW_SECONDS)
    assert evaluate_stage_freshness("bronze", max_ts, BRONZE_BUDGET_SECONDS, now) is None


def test_stage_ordinary_stale_keeps_plain_dedupe_key():
    """An ordinary stale alert keeps the unsuffixed key so the future-ts variant
    stays distinct from it."""
    now = datetime.now(UTC)
    max_ts = now - timedelta(seconds=BRONZE_BUDGET_SECONDS + 60)
    alert = evaluate_stage_freshness("bronze", max_ts, BRONZE_BUDGET_SECONDS, now)
    assert alert is not None
    assert alert.dedupe_key == "deadman_bronze"


# ---- calendar-aware session gate --------------------------------------------


def test_session_open_during_regular_hours():
    # 2026-06-11 is a Thursday; 15:00 UTC == 11:00 ET, inside the cash session.
    assert is_nyse_session_open(datetime(2026, 6, 11, 15, 0, tzinfo=UTC)) is True


def test_session_closed_overnight():
    # 02:00 UTC == 22:00 ET the prior evening — closed.
    assert is_nyse_session_open(datetime(2026, 6, 11, 2, 0, tzinfo=UTC)) is False


def test_session_closed_on_market_holiday():
    # 2026-01-01 (New Year's Day) is an NYSE holiday — even at 15:00 UTC
    # (a normal session minute) the calendar reports closed, which the old
    # weekday-only heuristic could not do.
    assert is_nyse_session_open(datetime(2026, 1, 1, 15, 0, tzinfo=UTC)) is False


def test_session_closed_on_weekend():
    # 2026-06-13 is a Saturday.
    assert is_nyse_session_open(datetime(2026, 6, 13, 15, 0, tzinfo=UTC)) is False


def test_session_naive_datetime_rejected():
    import pytest

    with pytest.raises(ValueError):
        is_nyse_session_open(datetime(2026, 6, 11, 15, 0))


async def test_stage_checks_suppressed_on_holiday(monkeypatch):
    """Calendar-aware suppression: on a market holiday during what a naive
    clock would call 'market hours', the stale-bronze stage check is suppressed
    (no stage alert) even though service-liveness checks still run."""
    holiday_market_minute = datetime(2026, 1, 1, 15, 0, tzinfo=UTC)
    await _add_bronze(received_age_seconds=BRONZE_BUDGET_SECONDS + 600, now=holiday_market_minute)

    sent = AsyncMock(return_value=True)
    with patch.object(dw, "send_discord_alert", sent):
        alerts = await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=holiday_market_minute)

    # No stage alerts on the holiday despite stale bronze.
    assert [a for a in alerts if a.kind == "stage"] == []


# ---- run_watchdog integration (in-memory SQLite via conftest) ---------------


def _test_sessionmaker() -> async_sessionmaker:
    return async_sessionmaker(db.engine, expire_on_commit=False)


async def _add_bronze(received_age_seconds: float, now: datetime) -> None:
    async with _test_sessionmaker()() as session:
        session.add(
            BronzeEvent(
                event_id=f"e_{received_age_seconds}",
                source="UW",
                event_type="UW_FLOW",
                ticker="AAPL",
                session="regular",
                schema_version="v1",
                event_ts_utc=now,
                received_ts_utc=now - timedelta(seconds=received_age_seconds),
                payload={},
                ingest={},
            )
        )
        await session.commit()


async def test_run_watchdog_never_registered_service_is_silent():
    """No liveness rows at all => no service alerts dispatched."""
    sent = AsyncMock(return_value=True)
    with patch.object(dw, "send_discord_alert", sent):
        alerts = await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=AFTER_HOURS_UTC)
    # No service rows, after hours so no stage alerts either.
    assert alerts == []
    sent.assert_not_awaited()


async def test_features_stage_is_informational_not_paged_when_empty():
    """gold_feature_events is not written by the live ingestion/execution path,
    so it is legitimately empty intraday — the watchdog logs 'features'
    freshness but must never page it (was a standing false positive). The real
    live stages still page when empty, proving the change is scoped to features."""
    now = MARKET_HOURS_UTC
    await _add_bronze(received_age_seconds=10, now=now)  # bronze fresh
    # Neither silver nor gold_feature_events seeded (both empty).

    sent = AsyncMock(return_value=True)
    with patch.object(dw, "send_discord_alert", sent):
        alerts = await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=now)

    names = [a.name for a in alerts]
    assert "features" not in names  # the fix: an empty features stage never pages
    assert "silver" in names  # a real live stage still pages when empty (non-regression)


async def test_run_watchdog_stale_market_service_alerts_during_market_hours_and_dispatches():
    # Publish a row, then age it past its budget.
    await publish_liveness("ingestion", cadence_budget_seconds=300)
    # Age the row relative to the evaluation clock we pass to run_watchdog.
    async with _test_sessionmaker()() as session:
        row = await session.get(ServiceLiveness, "ingestion")
        row.last_success_ts_utc = MARKET_HOURS_UTC - timedelta(seconds=1000)
        await session.commit()

    sent = AsyncMock(return_value=True)
    with patch.object(dw, "send_discord_alert", sent):
        alerts = await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=MARKET_HOURS_UTC)

    names = [a.name for a in alerts]
    assert "ingestion" in names
    sent.assert_awaited()
    # Dispatched with the per-service dedupe key.
    assert any(kwargs["dedupe_key"] == "deadman_ingestion" for _, kwargs in sent.call_args_list)


async def test_run_watchdog_stale_market_service_is_quiet_after_hours():
    await publish_liveness("ingestion", cadence_budget_seconds=300)
    async with _test_sessionmaker()() as session:
        row = await session.get(ServiceLiveness, "ingestion")
        row.last_success_ts_utc = AFTER_HOURS_UTC - timedelta(seconds=1000)
        await session.commit()

    sent = AsyncMock(return_value=True)
    with patch.object(dw, "send_discord_alert", sent):
        alerts = await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=AFTER_HOURS_UTC)

    assert [a for a in alerts if a.kind == "service"] == []
    sent.assert_not_awaited()


async def test_run_watchdog_retired_service_is_quiet_after_hours():
    await publish_liveness("meta_weekly", cadence_budget_seconds=300)
    async with _test_sessionmaker()() as session:
        row = await session.get(ServiceLiveness, "meta_weekly")
        row.last_success_ts_utc = AFTER_HOURS_UTC - timedelta(seconds=1000)
        await session.commit()

    sent = AsyncMock(return_value=True)
    with patch.object(dw, "send_discord_alert", sent):
        alerts = await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=AFTER_HOURS_UTC)

    assert [a for a in alerts if a.kind == "service"] == []
    sent.assert_not_awaited()


async def test_run_watchdog_reactivated_retired_service_alerts():
    """End-to-end: a retired name whose row is stamped AFTER its retirement
    cutoff (i.e. it genuinely published again) must resume paging through the
    full run_watchdog path, not just the pure decision function."""
    reactivated_now = datetime(2026, 8, 15, 2, 0, tzinfo=UTC)  # after-hours, post-cutoff
    await publish_liveness("meta_weekly", cadence_budget_seconds=300)
    async with _test_sessionmaker()() as session:
        row = await session.get(ServiceLiveness, "meta_weekly")
        # Anchor both timestamps to the injected clock — publish_liveness stamped
        # them with the real wall clock, which (being "later" than this test's
        # synthetic reactivated_now) would otherwise look future-dated to the
        # reactivation check and be rejected as corrupted data.
        row.last_success_ts_utc = reactivated_now - timedelta(seconds=1000)
        row.updated_at = reactivated_now - timedelta(seconds=1000)
        await session.commit()

    sent = AsyncMock(return_value=True)
    with patch.object(dw, "send_discord_alert", sent):
        alerts = await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=reactivated_now)

    assert [a.name for a in alerts if a.kind == "service"] == ["meta_weekly"]
    sent.assert_awaited()


async def test_run_watchdog_stage_alerts_only_during_market_hours():
    # Stale bronze (older than budget), no silver/features rows at all.
    await _add_bronze(received_age_seconds=BRONZE_BUDGET_SECONDS + 600, now=MARKET_HOURS_UTC)

    # During market hours: stage alerts fire.
    sent_open = AsyncMock(return_value=True)
    with patch.object(dw, "send_discord_alert", sent_open):
        open_alerts = await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=MARKET_HOURS_UTC)
    stage_names_open = {a.name for a in open_alerts if a.kind == "stage"}
    assert "bronze" in stage_names_open
    assert sent_open.await_count >= 1

    # After hours: same stale data, NO stage alerts.
    sent_closed = AsyncMock(return_value=True)
    with patch.object(dw, "send_discord_alert", sent_closed):
        closed_alerts = await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=AFTER_HOURS_UTC)
    assert [a for a in closed_alerts if a.kind == "stage"] == []


async def test_run_watchdog_fresh_stack_during_market_hours_no_alert():
    # Fresh bronze + silver + features so no stage is stale, and a fresh service.
    await _add_bronze(received_age_seconds=10, now=MARKET_HOURS_UTC)
    async with _test_sessionmaker()() as session:
        session.add(
            SilverSignal(
                signal_id="s1",
                ticker="AAPL",
                signal_type="FLOW_AGG_5M",
                signal_ts_utc=MARKET_HOURS_UTC,
                features={},
                created_at_utc=MARKET_HOURS_UTC - timedelta(seconds=10),
            )
        )
        await session.commit()
    await publish_liveness("ingestion", cadence_budget_seconds=300)
    # Pin the service row fresh relative to the evaluation clock.
    async with _test_sessionmaker()() as session:
        row = await session.get(ServiceLiveness, "ingestion")
        row.last_success_ts_utc = MARKET_HOURS_UTC - timedelta(seconds=10)
        await session.commit()

    sent = AsyncMock(return_value=True)
    with patch.object(dw, "send_discord_alert", sent):
        alerts = await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=MARKET_HOURS_UTC)

    # Service is fresh; bronze+silver fresh. features has no rows => that stage
    # WILL alert (no features produced during market hours is a real stall).
    # Assert the fresh stages did NOT alert and the service did not alert.
    names = {a.name for a in alerts}
    assert "ingestion" not in names
    assert "bronze" not in names
    assert "silver" not in names


# ---- global circuit breaker (latched-kill-switch visibility) ----------------
# The breaker latches with no auto-reset, and on 2026-08-13 it sat OPEN for 21h
# while ingestion stayed alive and bronze/silver stayed fresh — so every existing
# check here was green and nothing paged. It cost a full trading day (31
# full-consensus EXECUTE decisions blocked, zero orders) before being noticed by
# accident. A latched kill switch must be loud.


async def _set_breaker(status: str, details: str, updated_at: datetime) -> None:
    from orion.storage.models import SystemStatus

    async with _test_sessionmaker()() as session:
        await session.merge(
            SystemStatus(
                key="GLOBAL_CIRCUIT_BREAKER",
                status=status,
                details=details,
                last_updated_utc=updated_at,
            )
        )
        await session.commit()


def test_evaluate_circuit_breaker_closed_is_silent():
    from orion.storage.models import SystemStatus

    row = SystemStatus(key="GLOBAL_CIRCUIT_BREAKER", status="CLOSED", details="Reset by system/operator")
    assert dw.evaluate_circuit_breaker(row, MARKET_HOURS_UTC) is None


def test_evaluate_circuit_breaker_absent_row_is_silent():
    """No record means the breaker has never tripped — nominal, never invent an
    alert for a row that does not exist."""
    assert dw.evaluate_circuit_breaker(None, MARKET_HOURS_UTC) is None


def test_evaluate_circuit_breaker_open_reports_cause_and_age():
    """The alert must carry WHY it opened and HOW LONG it has been open — the
    two facts that would have made the 21-hour latch obvious."""
    from orion.storage.models import SystemStatus

    row = SystemStatus(
        key="GLOBAL_CIRCUIT_BREAKER",
        status="OPEN",
        details="CRITICAL: Heartbeat missing for 425.76s > 60.0s",
        last_updated_utc=MARKET_HOURS_UTC - timedelta(hours=21),
    )
    alert = dw.evaluate_circuit_breaker(row, MARKET_HOURS_UTC)
    assert alert is not None
    assert alert.kind == "breaker"
    assert alert.age_seconds == pytest.approx(21 * 3600)
    assert "Heartbeat missing" in alert.message
    assert "trading is halted" in alert.message.lower()


async def test_run_watchdog_alerts_when_breaker_open_after_hours():
    """NOT market-hours gated. A breaker latched overnight is precisely the case
    that went unnoticed, and the operator needs to know before the bell."""
    await _set_breaker("OPEN", "CRITICAL: Heartbeat missing for 425.76s > 60.0s", AFTER_HOURS_UTC)

    sent = AsyncMock(return_value=True)
    with patch.object(dw, "send_discord_alert", sent):
        alerts = await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=AFTER_HOURS_UTC)

    assert [a.name for a in alerts if a.kind == "breaker"] == ["circuit_breaker"]
    assert any(kwargs["dedupe_key"].startswith("deadman_circuit_breaker") for _, kwargs in sent.call_args_list)


async def test_run_watchdog_silent_when_breaker_closed():
    await _set_breaker("CLOSED", "Reset by system/operator", AFTER_HOURS_UTC)

    sent = AsyncMock(return_value=True)
    with patch.object(dw, "send_discord_alert", sent):
        alerts = await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=AFTER_HOURS_UTC)

    assert [a for a in alerts if a.kind == "breaker"] == []


async def test_breaker_alert_is_suppressed_on_the_next_five_minute_fire():
    """The watchdog is a 5-minute launchd one-shot, so in-memory dedupe resets
    every run. The cross-process state file must hold the 15-minute window —
    otherwise a latched breaker pages every 5 minutes and gets muted by the
    operator, which is how it goes unnoticed again."""
    await _set_breaker("OPEN", "CRITICAL: Heartbeat missing for 425.76s > 60.0s", AFTER_HOURS_UTC)

    sent = AsyncMock(return_value=True)
    with patch.object(dw, "send_discord_alert", sent):
        await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=AFTER_HOURS_UTC)
        first_dispatches = sent.await_count
        # Second fire, 5 minutes later: still detected, but not re-dispatched.
        alerts = await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=AFTER_HOURS_UTC + timedelta(minutes=5))

    assert [a.name for a in alerts if a.kind == "breaker"] == ["circuit_breaker"]
    assert sent.await_count == first_dispatches


async def test_breaker_alert_is_dispatched_even_when_a_later_read_fails():
    """The kill-switch page must be the one thing that always gets out.

    The breaker is detected early but dispatched at the end of the pass, after
    four ancillary stage/candidate queries. If one of those raises (schema skew,
    a slow or broken stage table) the whole pass aborted before dispatch and the
    latched breaker stayed silent — the exact failure mode this alert exists to
    prevent."""
    await _set_breaker("OPEN", "CRITICAL: Heartbeat missing for 425.76s > 60.0s", AFTER_HOURS_UTC)

    sent = AsyncMock(return_value=True)
    exploding_read = AsyncMock(side_effect=RuntimeError("stage query exploded"))
    with patch.object(dw, "send_discord_alert", sent), patch.object(dw, "_read_stage_max_ts", exploding_read):
        alerts = await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=AFTER_HOURS_UTC)

    assert [a.name for a in alerts if a.kind == "breaker"] == ["circuit_breaker"]
    assert any(kwargs["dedupe_key"].startswith("deadman_circuit_breaker") for _, kwargs in sent.call_args_list)


async def test_watchdog_reports_its_own_degradation_when_checks_fail():
    """A watchdog that swallows a read failure and reports 'healthy' is worse
    than one that crashes. If the ancillary checks cannot complete, say so."""
    exploding_read = AsyncMock(side_effect=RuntimeError("stage query exploded"))
    sent = AsyncMock(return_value=True)
    with patch.object(dw, "send_discord_alert", sent), patch.object(dw, "_read_stage_max_ts", exploding_read):
        alerts = await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=AFTER_HOURS_UTC)

    assert [a.name for a in alerts if a.kind == "watchdog"] == ["watchdog"]
    assert any("DEGRADED" in a.message for a in alerts if a.kind == "watchdog")


async def test_degraded_alert_dispatched_when_the_breaker_read_itself_fails():
    """If the breaker state cannot be read, the operator must be told the kill
    switch state is UNKNOWN. Unwinding the pass here would produce no breaker
    page AND no degradation page — the watchdog would just look quiet."""
    boom = AsyncMock(side_effect=RuntimeError("system_status read exploded"))
    sent = AsyncMock(return_value=True)
    with patch.object(dw, "send_discord_alert", sent), patch.object(dw, "_read_circuit_breaker", boom):
        alerts = await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=AFTER_HOURS_UTC)

    assert [a.name for a in alerts if a.kind == "watchdog"] == ["watchdog"]
    assert any("UNKNOWN" in a.message for a in alerts if a.kind == "watchdog")
    sent.assert_awaited()


async def test_a_reopened_breaker_is_not_suppressed_by_the_previous_incident():
    """Suppression must key off the incident, not the name. An operator who
    resets the breaker and sees it trip again 2 minutes later must be paged for
    the NEW trip, not muted by the 15-minute window from the old one."""
    await _set_breaker("OPEN", "first incident", AFTER_HOURS_UTC)

    sent = AsyncMock(return_value=True)
    with patch.object(dw, "send_discord_alert", sent):
        await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=AFTER_HOURS_UTC)
        after_first = sent.await_count

        # Operator resets; it re-opens 2 minutes later for a different reason.
        reopened_at = AFTER_HOURS_UTC + timedelta(minutes=2)
        await _set_breaker("OPEN", "second incident", reopened_at)
        await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=reopened_at)

    assert sent.await_count == after_first + 1


async def test_breaker_unknown_page_is_not_suppressed_by_an_unrelated_degradation():
    """The two degradation causes must not share a suppression key.

    A stage-read failure and 'breaker state is UNKNOWN' are different incidents
    with different urgency; if a stage failure pages first, the kill-switch-state
    page must still get out inside the same 15-minute window."""
    import json
    import os
    import pathlib
    from datetime import datetime as _datetime

    # A stage-check degradation already paged a minute ago, under the old shared key.
    pathlib.Path(os.environ["ORION_DEADMAN_STATE"]).write_text(
        json.dumps({"deadman_watchdog": _datetime.now(UTC).timestamp() - 60})
    )

    boom = AsyncMock(side_effect=RuntimeError("system_status read exploded"))
    sent = AsyncMock(return_value=True)
    with patch.object(dw, "send_discord_alert", sent), patch.object(dw, "_read_circuit_breaker", boom):
        await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=AFTER_HOURS_UTC)

    assert any("UNKNOWN" in args[0] for args, _ in sent.call_args_list), (
        "breaker-state-UNKNOWN page was suppressed by an unrelated watchdog degradation"
    )
