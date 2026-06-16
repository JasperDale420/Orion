from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from orion.storage.db import Base


class SignalType(str, enum.Enum):
    OHLCV_1M = "OHLCV_1M"
    FLOW_AGG_5M = "FLOW_AGG_5M"


class SilverSignal(Base):
    __tablename__ = "silver_signals"

    # Composite PK: ticker + timestamp + type (+ version/run_id explicitly or implicitly)
    # We'll use a synthetic ID but enforce uniqueness on the business key
    signal_id: Mapped[str] = mapped_column(String, primary_key=True)  # Deterministic ID

    ticker: Mapped[str] = mapped_column(String, nullable=False, index=True)
    signal_ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    signal_type: Mapped[str] = mapped_column(String, nullable=False)  # Enum as string

    # Store all calculated features here (open, close, rsi, vwap, etc.)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    created_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (Index("ix_silver_ticker_time", "ticker", "signal_ts_utc"),)
