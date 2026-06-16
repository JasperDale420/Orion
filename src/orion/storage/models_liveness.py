"""Service-liveness contract table.

One row per long-running Orion service. Each service upserts its row at the end
of every successful work cycle (see ``orion.shared.liveness.publish_liveness``),
advancing ``last_success_ts_utc`` and incrementing ``cycle_count``. The dead-man
watchdog (``orion.jobs.deadman_watchdog``) reads these rows and alerts when any
registered service's ``last_success_ts_utc`` is older than its own declared
``cadence_budget_seconds`` — a single, unified ABSENCE signal that replaces the
scattered heartbeat/lease/probe mechanisms.

A service is "registered" the moment it publishes for the first time; the
watchdog never alerts on a service it has never seen, so adding a new publisher
is the only step needed to bring a service under watch.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class ServiceLiveness(Base):
    __tablename__ = "service_liveness"

    # Logical service name, e.g. "ingestion", "execution", "feature_enrichment".
    service: Mapped[str] = mapped_column(String, primary_key=True)

    # When the service last completed a successful work cycle (tz-aware UTC).
    last_success_ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Monotonic count of successful cycles since the row was first created.
    cycle_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # Last error string the service chose to surface on its most recent publish
    # (nullable; a successful cycle clears it back to NULL).
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)

    # The service's own declared staleness budget in seconds. The watchdog
    # compares (now - last_success_ts_utc) against this value; older than the
    # budget is an alert.
    cadence_budget_seconds: Mapped[int] = mapped_column(Integer, nullable=False)

    # Row-update timestamp (server-maintained).
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
