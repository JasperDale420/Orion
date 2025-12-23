from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from orion.storage.db import Base


class OrderRecord(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    decision_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    candidate_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    ticker: Mapped[str] = mapped_column(String, nullable=False, index=True)
    side: Mapped[str] = mapped_column(String, nullable=False)

    qty: Mapped[float] = mapped_column(Float, nullable=False)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    client_order_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)

    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class FillRecord(Base):
    __tablename__ = "fills"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    ticker: Mapped[str] = mapped_column(String, nullable=False, index=True)
    broker_order_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    client_order_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    filled_qty: Mapped[float] = mapped_column(Float, nullable=False)
    filled_avg_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    side: Mapped[str | None] = mapped_column(String, nullable=True)

    filled_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ux_fills_broker_order_id", "broker_order_id", unique=True),)


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

    raw_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ix_positions_snapshots_ts_ticker", "snapshot_ts_utc", "ticker"),)
