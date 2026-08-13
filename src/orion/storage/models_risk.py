from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from orion.storage.db import Base


class RiskState(Base):
    """
    Persists daily risk metrics to survive restarts.
    Single row per day (or updated constantly).
    """

    __tablename__ = "risk_state"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. "global_risk_v1"
    # timezone=True so SQLAlchemy emits `TIMESTAMP WITH TIME ZONE` and the
    # asyncpg driver accepts the tz-aware datetime produced by
    # `datetime.now(UTC)`. Without it, an INSERT with a tz-aware default
    # raises asyncpg.DataError ("can't subtract offset-naive and
    # offset-aware datetimes") because the column is bound as
    # `TIMESTAMP WITHOUT TIME ZONE`.
    updated_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    current_daily_loss: Mapped[float | None] = mapped_column(Float, default=0.0)
    current_equity: Mapped[float | None] = mapped_column(Float, default=0.0)
    starting_equity: Mapped[float | None] = mapped_column(Float, default=0.0)
    peak_equity: Mapped[float | None] = mapped_column(Float, default=0.0)

    open_positions_count: Mapped[int | None] = mapped_column(Integer, default=0)

    # Accounting convention the money columns above were written under. NULL
    # means the row predates the option contract-multiplier fix, so its loss and
    # equity figures are at 1/100th scale and must not arm the kill switch.
    # RiskManager writes the current version on every save and refuses order
    # admission for a row that does not carry it.
    accounting_version: Mapped[int | None] = mapped_column(Integer, default=None)


class ProcessedFill(Base):
    """
    Tracks processed fill IDs (or client order IDs) to prevent duplicate processing on restart.
    """

    __tablename__ = "processed_fills"

    fill_id: Mapped[str] = mapped_column(String, primary_key=True)  # Unique ID from broker (or deduped ID)
    client_order_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    processed_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    ticker: Mapped[str | None] = mapped_column(String, nullable=True)
    qty: Mapped[float | None] = mapped_column(Float, nullable=True)


class PendingOrder(Base):
    """Persists in-flight orders so a restart doesn't lose pending exposure.

    Previously `RiskManager.pending_orders` was an in-memory dict only; the
    first ~30s after restart calculated projected exposure from positions
    only, ignoring orders submitted just before the crash. Each row mirrors
    the in-memory tuple `(ticker, signed_cost)` for a given client_order_id.

    On RiskManager.initialize, rows older than the configured TTL are
    discarded — most orders fill or fail within minutes; a row that
    survives an hour is almost certainly a stale write from a crashed-
    between-DB-write-and-broker-call edge case.
    """

    __tablename__ = "pending_orders"

    order_id: Mapped[str] = mapped_column(String, primary_key=True)
    ticker: Mapped[str] = mapped_column(String, nullable=False, index=True)
    signed_cost: Mapped[float] = mapped_column(Float, nullable=False)  # +cost on BUY, -cost on SELL
    created_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
