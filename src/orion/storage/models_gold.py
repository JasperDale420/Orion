import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Float, Index, String

from orion.storage.db import Base


class TradeDirection(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class CandidateTrade(Base):
    __tablename__ = "candidate_trades"

    # Deterministic ID: sha256(ticker + timestamp + rule_id)
    candidate_id = Column(String, primary_key=True)

    ticker = Column(String, nullable=False, index=True)
    timestamp_utc = Column(DateTime(timezone=True), nullable=False, index=True)

    rule_id = Column(String, nullable=False, index=True)
    direction = Column(String, nullable=False)  # LONG/SHORT

    # Confidence score (0.0 - 1.0) output by rule or ML meta-layer
    confidence = Column(Float, nullable=False, default=1.0)

    # Source of the signal (e.g. "UW", "ALPACA")
    source = Column(String, nullable=True)  # Added for SolverRouter context

    # Execution Params (Limit Price, etc) - Added for PolicyEngine
    execution_params = Column(JSON, nullable=True)

    # Pointers to evidence (signal_ids, event_ids)
    evidence = Column(JSON, nullable=False)

    created_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_candidate_ticker_time", "ticker", "timestamp_utc"),
        Index("ix_candidate_rule", "rule_id"),
    )


class ExitDecision(Base):
    """Track exit decisions triggered by exit rules."""

    __tablename__ = "exit_decisions"

    exit_id = Column(String, primary_key=True)
    ticker = Column(String, nullable=False, index=True)
    candidate_id = Column(String, nullable=True, index=True)  # Links to entry

    # Exit rule info
    rule_id = Column(String, nullable=False)  # Which exit rule triggered
    exit_reason = Column(String, nullable=False)
    urgency = Column(String, nullable=True)  # IMMEDIATE, SOON, CONSIDER
    confidence = Column(Float, nullable=True)
    details = Column(JSON, nullable=True)

    # Execution
    broker_order_id = Column(String, nullable=True)
    exit_ts_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    exit_price = Column(Float, nullable=True)

    # P&L tracking
    entry_price = Column(Float, nullable=True)
    pnl_usd = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)


class StrategyDecision(Base):
    __tablename__ = "strategy_decisions"

    decision_id = Column(String, primary_key=True)
    candidate_id = Column(String, index=True, nullable=False)
    timestamp_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Context
    ticker = Column(String, nullable=False)
    strategy_version_id = Column(String, nullable=False)
    model_version = Column(String, nullable=True)

    # Decision
    decision = Column(String, nullable=False)  # EXECUTE, SKIP
    p_take = Column(Float, nullable=True)
    execution_params = Column(JSON, nullable=True)  # Limit price logic, TIF, etc.

    reason = Column(String, nullable=True)  # Explanation

    executed_successfully = Column(String, nullable=True)  # "TRUE", "FALSE", "SKIPPED", "PENDING"

    # PRD 11.2 Trace
    decision_trace_json = Column(JSON, nullable=True)


class GoldTickerRollup(Base):
    """
    Aggregated OHLCV bars (5m, 1h, 1d) derived from Silver Signals.
    """

    __tablename__ = "gold_ticker_rollup"

    # Composite PK: ticker + period + timestamp
    ticker = Column(String, primary_key=True)
    period = Column(String, primary_key=True)  # 5m, 1h, 1d
    timestamp_utc = Column(DateTime(timezone=True), primary_key=True)

    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    vwap = Column(Float, nullable=False)

    created_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (Index("ix_gold_rollup_ticker_period_ts", "ticker", "period", "timestamp_utc"),)


class CandidateLabel(Base):
    """
    PRD 6.3: Triple Barrier Labels for Candidates.
    """

    __tablename__ = "candidate_labels"

    candidate_id = Column(String, primary_key=True)  # FK to candidate_trades
    timestamp_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    label = Column(Float, nullable=False)  # 1 (PT), -1 (SL), 0 (Time)
    ret = Column(Float, nullable=False)  # Return at barrier
    barrier_hit_ts = Column(DateTime(timezone=True), nullable=False)

    # PRD 8.2 required diagnostics
    time_to_hit_seconds = Column(Float, nullable=True)
    mfe = Column(Float, nullable=True)
    mae = Column(Float, nullable=True)


class LabelEvent(Base):
    """
    PRD 6.3: labels_event
    Event-level labels keyed by candidate_id, including forward returns and triple-barrier outputs.
    """

    __tablename__ = "labels_event"

    candidate_id = Column(String, primary_key=True)
    created_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    ticker = Column(String, nullable=False, index=True)
    event_ts_utc = Column(DateTime(timezone=True), nullable=False, index=True)

    # Forward returns (configurable horizons). Keys like "1m", "5m", "60m", ...
    forward_returns = Column(JSON, nullable=False, default=dict)

    # Triple-barrier outputs (mirror CandidateLabel)
    label = Column(Float, nullable=True)
    ret = Column(Float, nullable=True)
    barrier_hit_ts = Column(DateTime(timezone=True), nullable=True)
    time_to_hit_seconds = Column(Float, nullable=True)
    mfe = Column(Float, nullable=True)
    mae = Column(Float, nullable=True)

    # Auditability: labeling parameters + provenance
    label_config = Column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ix_labels_event_ticker_ts", "ticker", "event_ts_utc"),)


class LabelWindow(Base):
    """
    PRD 6.3: labels_window
    Window-level labels keyed by (ticker, period, window_end_ts_utc).
    """

    __tablename__ = "labels_window"

    ticker = Column(String, primary_key=True)
    period = Column(String, primary_key=True)  # e.g. "5m", "1h", "1d"
    window_end_ts_utc = Column(DateTime(timezone=True), primary_key=True)

    created_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    forward_returns = Column(JSON, nullable=False, default=dict)
    label_config = Column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ix_labels_window_ticker_period_ts", "ticker", "period", "window_end_ts_utc"),)


class GoldFeatureEvent(Base):
    """
    PRD 6.3: Point-in-time features (1m resolution or event-based).
    Persisted for model training and offline analysis.
    """

    __tablename__ = "gold_feature_events"

    # Composite PK
    ticker = Column(String, primary_key=True)
    event_ts_utc = Column(DateTime(timezone=True), primary_key=True)
    feature_set_id = Column(String, primary_key=True)  # e.g. "v1_legacy"

    # The actual feature vector
    features = Column(JSON, nullable=False)

    created_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (Index("ix_gold_feat_event_ticker_ts", "ticker", "event_ts_utc"),)


class GoldFeatureWindow(Base):
    """
    PRD 6.3: Window/Aggregated features (e.g. 5m, 1h).
    """

    __tablename__ = "gold_feature_windows"

    # Composite PK
    ticker = Column(String, primary_key=True)
    window_end_ts_utc = Column(DateTime(timezone=True), primary_key=True)
    period = Column(String, primary_key=True)  # e.g. "5m", "1h"
    feature_set_id = Column(String, primary_key=True)

    features = Column(JSON, nullable=False)

    created_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (Index("ix_gold_feat_window_ticker_ts", "ticker", "window_end_ts_utc"),)
