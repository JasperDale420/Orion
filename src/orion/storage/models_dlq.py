import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class DeadLetterQueue(Base):
    __tablename__ = "dead_letter_queue"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # Canonical envelope fields for idempotent replay (PRD 15.3)
    event_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    source_event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    event_type: Mapped[str | None] = mapped_column(String, nullable=True)
    ticker: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    event_ts_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Correlation / provenance
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)

    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str] = mapped_column(String, nullable=False)
    stack_trace: Mapped[str] = mapped_column(String, nullable=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="FAILED")  # FAILED, REPLAYED, IGNORED

    __table_args__ = (Index("ix_dlq_status_retry", "status", "retry_count"),)

    def __repr__(self) -> str:
        return f"<DeadLetterQueue(id={self.id}, error={self.error_message[:20]})>"
