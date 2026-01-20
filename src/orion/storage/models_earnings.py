"""Earnings calendar table model."""
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, Date, DateTime, Float, Index, String

from orion.storage.db import Base


class SilverEarningsCalendar(Base):
    """
    Earnings calendar from UW API.
    Tracks past and upcoming earnings for ML features.
    """

    __tablename__ = "silver_earnings_calendar"

    # Composite PK: ticker + report_date
    ticker = Column(String(20), primary_key=True)
    report_date = Column(Date, primary_key=True)

    # Timing
    announce_time = Column(String(20), nullable=True)  # 'premarket', 'afterhours', 'during'

    # Estimates and actuals
    eps_estimate = Column(Float, nullable=True)
    eps_actual = Column(Float, nullable=True)
    revenue_estimate = Column(BigInteger, nullable=True)
    revenue_actual = Column(BigInteger, nullable=True)

    # Metadata
    created_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_earnings_date", "report_date"),
        Index("idx_earnings_ticker_date", "ticker", "report_date"),
    )
