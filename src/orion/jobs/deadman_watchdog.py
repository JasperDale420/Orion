"""Dead-man watchdog — the unified liveness/ABSENCE guard (redesign R3 / RA.1).

Runs as a standalone launchd one-shot every 5 minutes. Two independent checks:

1. **Service liveness.** Reads every ``service_liveness`` row and alerts when
   ``now - last_success_ts_utc`` exceeds that service's own declared
   ``cadence_budget_seconds``. Market-session services such as ingestion and
   execution are informational outside the NYSE cash session; scheduled
   always-on jobs still alert around the clock. Services register themselves on
   their first publish, so a service the watchdog has never seen is silently
   skipped — never alerted on absence it cannot attribute.

2. **Pipeline-depth stage freshness (market-hours-gated, REAL data).** During
   the regular cash session it asserts per-stage freshness on the actual
   pipeline tables — ``max(bronze_events.received_ts_utc)`` and
   ``max(silver_signals.created_at_utc)`` — each against a per-stage staleness
   budget. ``gold_feature_events`` freshness and today's ``candidate_trades``
   count are logged for visibility but never paged: the live ingestion/execution
   path does not write ``gold_feature_events`` (only backtests / nightly
   meta-search / DLQ recovery do), so it is legitimately empty intraday and was
   a standing false positive. This detects every stall class in the incident history (redis flap,
   gold-poller OOM, born-stale, WS death) with ZERO contamination risk: no
   synthetic events are ever injected (the adversarial-review-banned design).
   Outside market hours the stage checks are informational only — no alerts —
   matching the ``market_open_dataflow_check`` convention.

The job must work even when the main stack is wedged, so it opens its own
lightweight async engine for the reads (a separate process). Session awareness
is calendar-aware (``exchange_calendars`` XNYS via ``is_nyse_session_open``) so
holidays and early closes never produce overnight false alerts — the reason
the watchdog was booted out 2026-06-11.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

import exchange_calendars as xcals
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from orion.config import system_settings
from orion.shared.alerts import send_discord_alert
from orion.shared.logger import setup_struct_logger
from orion.storage.models import BronzeEvent
from orion.storage.models_gold import CandidateTrade, GoldFeatureEvent
from orion.storage.models_liveness import ServiceLiveness
from orion.storage.models_silver import SilverSignal

logger = setup_struct_logger("orion.deadman")

# Per-stage pipeline-depth staleness budgets (seconds). These mirror the
# market_open_dataflow_check bronze budget and add silver/feature stages.
BRONZE_BUDGET_SECONDS = 300
SILVER_BUDGET_SECONDS = 600
FEATURES_BUDGET_SECONDS = 1200

# A stage's newest row should never be in the future. A small forward tolerance
# absorbs benign clock skew between the writer host and the watchdog; anything
# beyond it is a clock/data-quality bug that silently blinds the freshness check
# (a negative age can never exceed the budget), so we alert on it explicitly.
FUTURE_SKEW_SECONDS = 120

_NYSE_CALENDAR = "XNYS"
MARKET_HOURS_SERVICE_NAMES = frozenset(
    {
        "execution",
        "feature_enrichment",
        "ingestion",
        "position_monitor",
    }
)
RETIRED_SERVICE_NAMES = frozenset(
    {
        "eod_agent",
        "meta_labeler",
        "meta_search",
        "meta_weekly",
        "price_target_labeler",
    }
)


def is_nyse_session_open(now_utc: datetime, *, calendar_name: str = _NYSE_CALENDAR) -> bool:
    """True iff ``now_utc`` is inside a live NYSE regular session.

    Calendar-aware (``exchange_calendars``), so market holidays and early
    closes are respected — the reason the deadman was booted out 2026-06-11
    was overnight/holiday false alerts from a weekday-only heuristic. Outside
    a real session this returns False and the per-stage pipeline freshness
    checks are suppressed entirely; only service-liveness budgets still alert.
    ``now_utc`` must be timezone-aware.
    """
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    cal = xcals.get_calendar(calendar_name)
    return bool(cal.is_open_on_minute(now_utc))


class Severity(str, Enum):
    OK = "OK"
    ALERT = "ALERT"


@dataclass(frozen=True)
class LivenessAlert:
    """A single dead-man finding: a stalled service or a stale pipeline stage."""

    kind: str  # "service" or "stage"
    name: str  # service name or stage name
    age_seconds: float | None  # None = never seen / no rows
    budget_seconds: int
    message: str
    # Distinguishes alert variants for the same stage/service so a distinct
    # failure mode (e.g. future-dated rows vs ordinary staleness) gets its own
    # cross-process suppression key instead of masking the other for 15 min.
    dedupe_suffix: str = ""

    @property
    def dedupe_key(self) -> str:
        return f"deadman_{self.name}{self.dedupe_suffix}"


def evaluate_service_liveness(
    rows: list[ServiceLiveness],
    now_utc: datetime,
    *,
    market_open: bool = True,
) -> list[LivenessAlert]:
    """Pure decision function for the service-liveness check.

    Alerts for each registered service whose newest successful cycle is older
    than its own declared budget. A service with no row is not represented here
    at all (the watchdog never invents absence for a service it has never seen).
    Market-session services are informational outside the cash session because
    those loops can legitimately stop publishing when there is no market data to
    process.
    """
    alerts: list[LivenessAlert] = []
    for row in rows:
        if row.service in RETIRED_SERVICE_NAMES:
            continue
        age = _service_age_seconds(row, now_utc)
        if age > row.cadence_budget_seconds:
            if not market_open and row.service in MARKET_HOURS_SERVICE_NAMES:
                continue
            alerts.append(
                LivenessAlert(
                    kind="service",
                    name=row.service,
                    age_seconds=age,
                    budget_seconds=row.cadence_budget_seconds,
                    message=(
                        f"service '{row.service}' has not published a successful cycle in "
                        f"{age:.0f}s (budget {row.cadence_budget_seconds}s, "
                        f"cycle_count={row.cycle_count}"
                        + (f", last_error={row.last_error}" if row.last_error else "")
                        + ") — the loop is wedged or the process is down"
                    ),
                )
            )
    return alerts


def _service_age_seconds(row: ServiceLiveness, now_utc: datetime) -> float:
    last = row.last_success_ts_utc
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return (now_utc - last).total_seconds()


def _suppressed_market_service_names(rows: list[ServiceLiveness], now_utc: datetime) -> list[str]:
    return [
        row.service
        for row in rows
        if row.service in MARKET_HOURS_SERVICE_NAMES and _service_age_seconds(row, now_utc) > row.cadence_budget_seconds
    ]


def evaluate_stage_freshness(
    stage_name: str,
    max_ts: datetime | None,
    budget_seconds: int,
    now_utc: datetime,
) -> LivenessAlert | None:
    """Pure decision function for one pipeline stage. Returns an alert when the
    newest row is missing or older than ``budget_seconds``; else ``None``."""
    if max_ts is None:
        return LivenessAlert(
            kind="stage",
            name=stage_name,
            age_seconds=None,
            budget_seconds=budget_seconds,
            message=(
                f"pipeline stage '{stage_name}' has NO rows during market hours — "
                "the stage is producing no output (full stall)"
            ),
        )
    if max_ts.tzinfo is None:
        max_ts = max_ts.replace(tzinfo=UTC)
    age = (now_utc - max_ts).total_seconds()
    if max_ts > now_utc + timedelta(seconds=FUTURE_SKEW_SECONDS):
        return LivenessAlert(
            kind="stage",
            name=stage_name,
            age_seconds=age,
            budget_seconds=budget_seconds,
            dedupe_suffix="_future_ts",
            message=(
                f"pipeline stage '{stage_name}' has FUTURE-DATED rows — newest row is "
                f"{-age:.0f}s in the future (tolerance {FUTURE_SKEW_SECONDS}s); "
                "this indicates a clock or data-quality bug. The freshness check is "
                "UNRELIABLE for this stage because a negative age can never exceed the "
                "staleness budget — the stale-data alarm is effectively disabled until fixed"
            ),
        )
    if age > budget_seconds:
        return LivenessAlert(
            kind="stage",
            name=stage_name,
            age_seconds=age,
            budget_seconds=budget_seconds,
            message=(
                f"pipeline stage '{stage_name}' is STALE during market hours — newest "
                f"row is {age:.0f}s old (budget {budget_seconds}s); the pipeline is "
                "stalled at or before this stage"
            ),
        )
    return None


def _make_engine() -> AsyncEngine:
    """Lightweight standalone engine for the watchdog process.

    Deliberately NOT the shared ``orion.storage.db.engine`` — the watchdog must
    survive a wedged main stack, so it owns its own short-lived connection.
    """
    return create_async_engine(system_settings.db_url, pool_pre_ping=True)


async def _read_liveness_rows(sessionmaker: async_sessionmaker) -> list[ServiceLiveness]:
    async with sessionmaker() as session:
        result = await session.execute(select(ServiceLiveness))
        return list(result.scalars().all())


async def _read_stage_max_ts(sessionmaker: async_sessionmaker, column) -> datetime | None:
    async with sessionmaker() as session:
        result = await session.execute(select(func.max(column)))
        return result.scalar_one_or_none()


async def _read_candidates_today(sessionmaker: async_sessionmaker, now_utc: datetime) -> int:
    start_of_day = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    async with sessionmaker() as session:
        result = await session.execute(
            select(func.count()).select_from(CandidateTrade).where(CandidateTrade.created_at_utc >= start_of_day)
        )
        return int(result.scalar_one())


async def run_watchdog(
    sessionmaker: async_sessionmaker | None = None,
    now_utc: datetime | None = None,
) -> list[LivenessAlert]:
    """Run one watchdog pass. Returns the alerts dispatched (empty when healthy).

    ``sessionmaker`` is injectable for tests; production builds a standalone
    engine. Alerts go to Discord with a per-name dedupe key (15-min window) and
    an ERROR log. The market-hours gate suppresses stage alerts and market-bound
    service alerts off-session; scheduled always-on service alerts still fire.
    """
    now = now_utc or datetime.now(UTC)
    own_engine: AsyncEngine | None = None
    if sessionmaker is None:
        own_engine = _make_engine()
        sessionmaker = async_sessionmaker(own_engine, expire_on_commit=False)

    try:
        alerts: list[LivenessAlert] = []

        market_open = is_nyse_session_open(now)

        # 1. Service liveness.
        rows = await _read_liveness_rows(sessionmaker)
        alerts.extend(evaluate_service_liveness(rows, now, market_open=market_open))
        if not market_open:
            suppressed_services = _suppressed_market_service_names(rows, now)
            if suppressed_services:
                logger.info(
                    "deadman_market_service_check_informational",
                    market_open=False,
                    suppressed_services=suppressed_services,
                )

        # 2. Pipeline-depth stage freshness — REAL data, market-hours gated.
        # Calendar-aware: outside a live NYSE session (nights, weekends,
        # holidays, early closes) the stage checks are suppressed entirely.
        bronze_max = await _read_stage_max_ts(sessionmaker, BronzeEvent.received_ts_utc)
        silver_max = await _read_stage_max_ts(sessionmaker, SilverSignal.created_at_utc)
        features_max = await _read_stage_max_ts(sessionmaker, GoldFeatureEvent.created_at_utc)
        candidates_today = await _read_candidates_today(sessionmaker, now)

        stage_findings = [
            evaluate_stage_freshness("bronze", bronze_max, BRONZE_BUDGET_SECONDS, now),
            evaluate_stage_freshness("silver", silver_max, SILVER_BUDGET_SECONDS, now),
        ]
        stage_alerts = [a for a in stage_findings if a is not None]

        # gold_feature_events is written only by backtests / nightly meta-search /
        # DLQ recovery — never by the live ingestion/execution path — so it is
        # legitimately empty during the cash session. Evaluate its freshness for
        # visibility but never page on it (it was a standing false positive that
        # paged "features has NO rows" every cycle intraday).
        features_finding = evaluate_stage_freshness("features", features_max, FEATURES_BUDGET_SECONDS, now)
        if features_finding is not None:
            logger.info(
                "deadman_features_stage_informational",
                features_max=str(features_max),
                age_seconds=features_finding.age_seconds,
                message=features_finding.message,
            )

        if market_open:
            alerts.extend(stage_alerts)
        else:
            # Outside the cash session the stage checks are informational only.
            logger.info(
                "deadman_stage_check_informational",
                market_open=False,
                bronze_max=str(bronze_max),
                silver_max=str(silver_max),
                features_max=str(features_max),
                candidates_today=candidates_today,
                stage_findings=[a.name for a in stage_alerts],
            )

        # candidates-today is informational only (count, never an alert).
        logger.info(
            "deadman_candidates_today",
            market_open=market_open,
            candidates_today=candidates_today,
        )

        alert_state = _load_alert_state()
        now_epoch = datetime.now(UTC).timestamp()
        for alert in alerts:
            logger.error(
                "deadman_alert",
                kind=alert.kind,
                name=alert.name,
                age_seconds=alert.age_seconds,
                budget_seconds=alert.budget_seconds,
                message=alert.message,
            )
            last_sent = alert_state.get(alert.dedupe_key, 0.0)
            if now_epoch - last_sent < _ALERT_SUPPRESSION_SECONDS:
                logger.info("deadman_alert_suppressed", dedupe_key=alert.dedupe_key)
                continue
            if await send_discord_alert(alert.message, dedupe_key=alert.dedupe_key):
                alert_state[alert.dedupe_key] = now_epoch
        _save_alert_state(alert_state)

        if not alerts:
            logger.info("deadman_healthy", services_checked=len(rows), market_open=market_open)

        return alerts
    finally:
        if own_engine is not None:
            await own_engine.dispose()


# ── Cross-process alert suppression ──────────────────────────────────────────
# The watchdog runs as a 5-minute launchd ONE-SHOT, so shared.alerts' in-memory
# 15-minute dedupe resets on every fire (adversarial-review finding: a stale
# service would alert every 5 minutes, not every 15). Persist last-alert
# timestamps to a small state file so suppression survives across processes.
_ALERT_SUPPRESSION_SECONDS = 900.0


def _alert_state_path() -> Path:
    # Resolved lazily so tests can point ORION_DEADMAN_STATE at a tmp dir.
    return Path(os.environ.get("ORION_DEADMAN_STATE", "logs/deadman_state.json"))


def _load_alert_state() -> dict[str, float]:
    try:
        return {str(k): float(v) for k, v in json.loads(_alert_state_path().read_text()).items()}
    except Exception:
        return {}


def _save_alert_state(state: dict[str, float]) -> None:
    try:
        path = _alert_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))
    except Exception as exc:  # noqa: BLE001 — best-effort; never break the probe
        logger.debug("deadman_state_save_failed", error=str(exc))


def main() -> int:
    """Entry point for `python -m orion.jobs.deadman_watchdog`.

    Exit 2 when any alert was dispatched, 0 otherwise. As a launchd one-shot a
    non-zero exit must NOT cause a restart loop — the plist omits KeepAlive and
    relies on the next StartInterval fire.
    """
    alerts = asyncio.run(run_watchdog())
    return 2 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())
