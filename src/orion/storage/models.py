from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class BronzeEvent(Base):
    __tablename__ = "bronze_events"

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String, nullable=False)  # "UW", "ALPACA"
    source_event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)  # "UW_FLOW", "ALPACA_BAR_1M"
    ticker: Mapped[str] = mapped_column(String, nullable=True, index=True)
    trading_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    session: Mapped[str] = mapped_column(String, nullable=False)
    schema_version: Mapped[str] = mapped_column(String, default="v1")
    event_ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    ingest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # Indexes for common queries
    __table_args__ = (
        Index("idx_bronze_events_ts", "event_ts_utc"),
        Index("idx_bronze_events_source_type", "source", "event_type"),
        Index("idx_bronze_events_ticker_ts", "ticker", "event_ts_utc"),
    )

    def __repr__(self) -> str:
        return f"<BronzeEvent(id={self.event_id}, source={self.source}, type={self.event_type})>"


class SystemStatus(Base):
    __tablename__ = "system_status"

    key: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. "global_health"
    status: Mapped[str] = mapped_column(String, nullable=False)  # "HEALTHY", "UNHEALTHY"
    last_updated_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    details: Mapped[str] = mapped_column(String, nullable=True)


class IngestWatermark(Base):
    __tablename__ = "ingest_watermarks"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    last_seen_ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_ts_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class JobCursorState(Base):
    __tablename__ = "job_cursor_state"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    last_seen_ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_id: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_ts_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class RuntimeConfig(Base):
    __tablename__ = "runtime_config"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_ts_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
