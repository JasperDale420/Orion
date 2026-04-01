import enum
from datetime import UTC, datetime

from sqlalchemy import JSON, Column, DateTime, Index, String

from orion.storage.db import Base


class SignalType(str, enum.Enum):
    OHLCV_1M = "OHLCV_1M"
    FLOW_AGG_5M = "FLOW_AGG_5M"


class SilverSignal(Base):
    __tablename__ = "silver_signals"

    # Composite PK: ticker + timestamp + type (+ version/run_id explicitly or implicitly)
    # We'll use a synthetic ID but enforce uniqueness on the business key
    signal_id = Column(String, primary_key=True)  # Deterministic ID

    ticker = Column(String, nullable=False, index=True)
    signal_ts_utc = Column(DateTime(timezone=True), nullable=False, index=True)
    signal_type = Column(String, nullable=False)  # Enum as string

    # Store all calculated features here (open, close, rsi, vwap, etc.)
    features = Column(JSON, nullable=False)

    created_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (Index("ix_silver_ticker_time", "ticker", "signal_ts_utc"),)
