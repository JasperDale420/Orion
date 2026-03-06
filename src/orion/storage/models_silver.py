import enum
from datetime import UTC, datetime

from sqlalchemy import JSON, Column, DateTime, Float, Index, Integer, String

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


class SilverUWAlert(Base):
    """
    PRD 6.2: UW Flow Alerts (minimum)
    """

    __tablename__ = "silver_uw_alerts"

    event_id = Column(String, primary_key=True)
    source_event_id = Column(String, nullable=True)
    ticker = Column(String, nullable=False, index=True)
    alert_ts_utc = Column(DateTime(timezone=True), nullable=False, index=True)

    put_call = Column(String(1), nullable=True)
    expiry = Column(String, nullable=True)
    strike = Column(Float, nullable=True)

    option_price = Column(Float, nullable=True)
    size_contracts = Column(Integer, nullable=True)
    premium_usd = Column(Float, nullable=True)

    volume_contract = Column(Float, nullable=True)
    open_interest = Column(Float, nullable=True)

    flags_json = Column(JSON, nullable=True)
    alert_tags = Column(JSON, nullable=True)
    ingest = Column(JSON, nullable=False, default=dict)

    created_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (Index("ix_silver_alerts_ticker_time", "ticker", "alert_ts_utc"),)
