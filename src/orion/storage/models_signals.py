from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from orion.storage.db import Base


class SignalLive(Base):
    """
    PRD.md §11.2: `signals_live` rows emitted for executable signals, with full decision trace + evidence pointers.
    """

    __tablename__ = "signals_live"

    signal_id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String, nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String, nullable=False)

    rule_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    model_version: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    expected_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_take: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    entry_logic: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    exit_rules: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    decision_trace_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ix_signals_live_ticker_time", "ticker", "timestamp_utc"),)
