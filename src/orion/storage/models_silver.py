import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Index, Integer, String

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

    created_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (Index("ix_silver_ticker_time", "ticker", "signal_ts_utc"),)


class SilverOptionFlow(Base):
    """
    PRD 6.2: UW Options Flow Schema
    Silver layer for normalized options flow events.
    """

    __tablename__ = "silver_uw_flow"

    # Primary Key: event_id from bronze/provider
    event_id = Column(String, primary_key=True)
    source_event_id = Column(String, nullable=True)

    ticker = Column(String, nullable=False, index=True)
    flow_ts_utc = Column(DateTime(timezone=True), nullable=False, index=True)

    # Contract Details
    put_call = Column(String(1), nullable=False)  # 'C' or 'P'
    expiry = Column(String, nullable=False)  # YYYY-MM-DD
    strike = Column(Float, nullable=False)

    # Pricing & Size
    option_price = Column(Float, nullable=False)
    size_contracts = Column(Integer, nullable=False)  # Contracts are discrete
    premium_usd = Column(Float, nullable=False)

    # Market Context
    bid = Column(Float, nullable=True)
    ask = Column(Float, nullable=True)
    underlying_price = Column(Float, nullable=True)

    # Flags/Meta
    aggressor = Column(String, nullable=True)  # ASK, BID, MID
    is_sweep = Column(Boolean, nullable=True)  # Whether this is a sweep trade

    # Use JSON for flags to handle variations? Or explicit columns as per PRD "minimum"?
    # PRD lists specifics. Let's do explicit columns for PRD specified fields.
    flags_json = Column(JSON, nullable=True)  # is_sweep, is_block, etc.

    volume_contract = Column(Float, nullable=True)
    open_interest = Column(Float, nullable=True)

    # Additional UW fields (added to capture more raw data)
    iv = Column(Float, nullable=True)  # Implied volatility
    volume_oi_ratio = Column(Float, nullable=True)  # Volume / Open Interest
    trade_count = Column(Integer, nullable=True)  # Number of trades in flow
    alert_rule = Column(String, nullable=True)  # UW alert type (RepeatedHits, etc.)
    option_chain = Column(String, nullable=True)  # Full OCC symbol

    # ML Feature Fields (added for feature engineering)
    ask_volume = Column(Integer, nullable=True)  # Volume at ask (buyer pressure)
    bid_volume = Column(Integer, nullable=True)  # Volume at bid (seller pressure)
    delta_diff = Column(Float, nullable=True)  # Delta change
    iv_change = Column(Float, nullable=True)  # IV momentum
    multi_leg_vol_ratio = Column(Float, nullable=True)  # Spread indicator
    alert_name = Column(String, nullable=True)  # Alert classification (e.g., "High Conviction Puts 1-14d")
    noti_type = Column(String, nullable=True)  # Notification type (flow_alerts, etc.)

    # Alpaca Greeks (captured at ingestion for point-in-time fidelity)
    delta_alpaca = Column(Float, nullable=True)
    gamma_alpaca = Column(Float, nullable=True)
    theta_alpaca = Column(Float, nullable=True)
    vega_alpaca = Column(Float, nullable=True)
    rho_alpaca = Column(Float, nullable=True)
    iv_alpaca = Column(Float, nullable=True)

    ingest = Column(JSON, nullable=False, default=dict)

    created_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (Index("ix_silver_flow_ticker_time", "ticker", "flow_ts_utc"),)


class SilverDarkPool(Base):
    """
    PRD 6.2: UW Dark Pool Schema
    """

    __tablename__ = "silver_uw_darkpool"

    event_id = Column(String, primary_key=True)
    source_event_id = Column(String, nullable=True)
    ticker = Column(String, nullable=False, index=True)
    dark_ts_utc = Column(DateTime(timezone=True), nullable=False, index=True)

    trade_price = Column(Float, nullable=False)
    size_shares = Column(Float, nullable=False)
    venue = Column(String, nullable=True)
    conditions = Column(String, nullable=True)  # Comma-sep string or JSON
    ingest = Column(JSON, nullable=False, default=dict)

    created_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SilverAlpacaBar(Base):
    """
    PRD 6.2: Alpaca 1m Bars
    """

    __tablename__ = "silver_alpaca_bars"

    # Composite PK: ticker + time
    ticker = Column(String, primary_key=True)
    bar_start_ts_utc = Column(DateTime(timezone=True), primary_key=True)

    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    vwap = Column(Float, nullable=True)
    ingest = Column(JSON, nullable=False, default=dict)

    created_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


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

    created_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (Index("ix_silver_alerts_ticker_time", "ticker", "alert_ts_utc"),)


class SilverOptionQuote(Base):
    """
    Real option quotes from Alpaca API at checkpoint intervals.
    Used for accurate ML labeling instead of modeled prices.
    """

    __tablename__ = "silver_option_quotes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    option_symbol = Column(String(32), nullable=False, index=True)
    underlying_ticker = Column(String(10), nullable=False, index=True)
    flow_event_id = Column(String(64), nullable=False, index=True)
    checkpoint = Column(String(10), nullable=False)  # 'entry', '15m', '30m', '1h', etc.
    ts_utc = Column(DateTime(timezone=True), nullable=False)

    # Price data
    bid_price = Column(Float, nullable=True)
    ask_price = Column(Float, nullable=True)
    mid_price = Column(Float, nullable=True)
    last_trade_price = Column(Float, nullable=True)

    # Greeks at checkpoint
    delta = Column(Float, nullable=True)
    gamma = Column(Float, nullable=True)
    theta = Column(Float, nullable=True)
    vega = Column(Float, nullable=True)
    iv = Column(Float, nullable=True)

    created_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_option_quotes_event_checkpoint", "flow_event_id", "checkpoint"),
        {"extend_existing": True},
    )
