from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String

from orion.storage.db import Base


class RiskState(Base):
    """
    Persists daily risk metrics to survive restarts.
    Single row per day (or updated constantly).
    """

    __tablename__ = "risk_state"

    id = Column(String, primary_key=True)  # e.g. "global_risk_v1"
    updated_at_utc = Column(DateTime, default=datetime.utcnow)

    current_daily_loss = Column(Float, default=0.0)
    current_equity = Column(Float, default=0.0)
    starting_equity = Column(Float, default=0.0)
    peak_equity = Column(Float, default=0.0)

    open_positions_count = Column(Integer, default=0)


class ProcessedFill(Base):
    """
    Tracks processed fill IDs (or client order IDs) to prevent duplicate processing on restart.
    """

    __tablename__ = "processed_fills"

    fill_id = Column(String, primary_key=True)  # Unique ID from broker (or deduped ID)
    client_order_id = Column(String, index=True, nullable=True)
    processed_at_utc = Column(DateTime, default=datetime.utcnow)

    ticker = Column(String, nullable=True)
    qty = Column(Float, nullable=True)
