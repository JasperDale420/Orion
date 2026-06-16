from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from orion.storage.db import Base


class OrderRecord(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    decision_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    candidate_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    ticker: Mapped[str] = mapped_column(String, nullable=False, index=True)
    side: Mapped[str] = mapped_column(String, nullable=False)

    qty: Mapped[float] = mapped_column(Float, nullable=False)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    client_order_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)

    # System attribution — identifies which trading system placed this order.
    # Used to filter positions in the shared Alpaca paper account.
    system: Mapped[str] = mapped_column(String, nullable=False, default="orion")

    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class FillRecord(Base):
    __tablename__ = "fills"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    ticker: Mapped[str] = mapped_column(String, nullable=False, index=True)
    broker_order_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    client_order_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    filled_qty: Mapped[float] = mapped_column(Float, nullable=False)
    filled_avg_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    side: Mapped[str | None] = mapped_column(String, nullable=True)

    filled_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ux_fills_broker_order_id", "broker_order_id", unique=True),)


class PositionRunningStats(Base):
    """Persisted running-window stats per open position.

    PositionMonitor.sync_positions accumulates `max_return_pct` and
    `max_drawdown_pct` on the in-memory `TrackedPosition` dataclass on
    every cycle, but those values were lost on container restart.
    After a restart, `_track_new_position` re-seeded them as
    `max(0, unrealized_pnl_pct)` / `min(0, ...)` — so a position
    that had hit +200% and was now at +50% looked to the ML exit
    classifier like it had a +50% max-return-so-far (no peak), and
    the classifier saw the feature vector of a brand-new stable
    trade. Together with several other defaults this kept the
    classifier's exit probability below the 0.55 threshold for
    every re-hydrated position (Bug #3 of exit-pipeline RCA — see
    `docs/rca/exit_classifier/SYNTHESIS.md`).

    This table is the durable side of those running stats. Upserted
    on every `sync_positions` tick by symbol; consulted on new-
    position tracking to rehydrate. Rows persist past the position
    close — when the symbol next appears in broker positions, the
    row primes the in-memory state. A symbol that's been closed
    permanently leaves a stale row, which is fine: it only gets read
    if/when a NEW position with the same symbol opens (and the next
    `sync_positions` cycle will immediately update it with the
    re-opened position's running window).
    """

    __tablename__ = "position_running_stats"

    # Per-symbol PK (matches TrackedPosition.symbol — OCC option
    # symbol for options, ticker for equity).
    symbol: Mapped[str] = mapped_column(String, primary_key=True)

    # Peak return and trough drawdown observed since the position
    # was first tracked. Both in percent units (100.0 = +100%) to
    # match TrackedPosition.max_return_pct / max_drawdown_pct.
    max_return_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    last_updated_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class PositionSnapshot(Base):
    """
    PRDv2 6.2/12.4: Persist positions snapshots once PAPER/LIVE is enabled.
    One row per ticker position per snapshot.
    """

    __tablename__ = "positions_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    snapshot_ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    ticker: Mapped[str] = mapped_column(String, nullable=False, index=True)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    avg_entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    market_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unrealized_pl: Mapped[float | None] = mapped_column(Float, nullable=True)

    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ix_positions_snapshots_ts_ticker", "snapshot_ts_utc", "ticker"),)
