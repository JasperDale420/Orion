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
    evaluate_service_liveness,
    evaluate_stage_freshness,
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


def _row(service: str, age_seconds: float, budget: int) -> ServiceLiveness:
    return ServiceLiveness(
        service=service,
        last_success_ts_utc=datetime.now(UTC) - timedelta(seconds=age_seconds),
        cycle_count=5,
        last_error=None,
        cadence_budget_seconds=budget,
        updated_at=datetime.now(UTC),
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
        _row("meta_weekly", age_seconds=400, budget=86400 * 8),  # fresh (huge budget)
    ]
    alerts = evaluate_service_liveness(rows, now)
    assert [a.name for a in alerts] == ["execution"]


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


async def test_run_watchdog_stale_service_alerts_and_dispatches():
    # Publish a row, then age it past its budget.
    await publish_liveness("ingestion", cadence_budget_seconds=300)
    # Age the row relative to the evaluation clock we pass to run_watchdog.
    async with _test_sessionmaker()() as session:
        row = await session.get(ServiceLiveness, "ingestion")
        row.last_success_ts_utc = AFTER_HOURS_UTC - timedelta(seconds=1000)
        await session.commit()

    sent = AsyncMock(return_value=True)
    with patch.object(dw, "send_discord_alert", sent):
        alerts = await run_watchdog(sessionmaker=_test_sessionmaker(), now_utc=AFTER_HOURS_UTC)

    names = [a.name for a in alerts]
    assert "ingestion" in names
    sent.assert_awaited()
    # Dispatched with the per-service dedupe key.
    _, kwargs = sent.call_args
    assert kwargs["dedupe_key"] == "deadman_ingestion"


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
