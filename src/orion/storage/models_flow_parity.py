"""Flow-push shadow-mode parity table (redesign R1 / task B1).

In ``ORION_FLOW_SOURCE=shadow`` mode the ingestion loop drains UW flow from BOTH
the Gateway WS push path and the Heber-Silver poll path, then records — per cycle
— how the two event_id sets compare. The deduper still collapses the union so
the pipeline sees each event once; this table is purely the measurement that
gates the C4 cutover to ``push``.

One row per ingestion cycle. Writes are best-effort: a parity-logging failure
must never take down ingestion.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class FlowPushParity(Base):
    __tablename__ = "flow_push_parity"

    # BigInteger on Postgres (production); Integer on SQLite so the rowid
    # autoincrements under the in-memory test DB (SQLite only auto-assigns a PK
    # for INTEGER PRIMARY KEY, not BIGINT).
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)

    # Wall-clock time of the ingestion cycle that produced this row.
    cycle_ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    # Distinct flow event_ids each path delivered this cycle (after the shared
    # born-stale freshness drop, so the comparison is fair).
    push_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    poll_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Overlap of the two id sets.
    matched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # poll_ids - push_ids: events poll caught that push missed. THE cutover gate
    # metric (must be 0 across the qualifying window, excluding born-stale).
    missed_by_push_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # push_ids - poll_ids: push-only events (expected — near-real-time arrivals
    # poll hasn't read from Silver yet).
    missed_by_poll_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Poll events whose id is a `uwflow_*` deterministic fallback (no Silver
    # event_id) — these can never match a push blake2b id (§4.2). Non-zero
    # blocks cutover.
    parity_unmatchable_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Median push-vs-poll arrival latency improvement in seconds for matched
    # ids (push should lead poll by the parquet round-trip). NULL when no match.
    median_latency_improvement_s: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Lag-tolerant reconciliation window (seconds) used to classify
    # matched/missed for this row. Push and Heber-poll legitimately surface the
    # same event in DIFFERENT cycles (push in cycle N; Silver lands it so poll
    # sees it in cycle N+1). A poll id is only counted missed_by_push once this
    # window has fully elapsed with no matching push delivery, so the cutover
    # gate isn't false-failed by ordinary rsync/silver lag.
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Sample of missed-by-push event_ids (JSON list, capped) for forensics.
    missed_by_push_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)

    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
