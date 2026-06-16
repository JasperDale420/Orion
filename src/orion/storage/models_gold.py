from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from orion.storage.db import Base


class TradeDirection(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class CandidateTrade(Base):
    __tablename__ = "candidate_trades"

    # Deterministic ID: sha256(ticker + timestamp + rule_id)
    candidate_id: Mapped[str] = mapped_column(String, primary_key=True)

    ticker: Mapped[str] = mapped_column(String, nullable=False, index=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    rule_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String, nullable=False)  # LONG/SHORT

    # Confidence score (0.0 - 1.0) output by rule or ML meta-layer
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    # Source of the signal (e.g. "UW", "ALPACA")
    source: Mapped[str | None] = mapped_column(String, nullable=True)  # Added for SolverRouter context

    # Options-specific fields (nullable for backward compatibility)
    option_symbol: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )  # OCC format: AAPL240419C00190000
    strike_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    expiration_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    option_type: Mapped[str | None] = mapped_column(String, nullable=True)  # CALL or PUT
    underlying_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # Price at signal time
    premium: Mapped[float | None] = mapped_column(Float, nullable=True)  # Contract premium from UW flow

    # Execution Params (Limit Price, etc) - Added for PolicyEngine
    execution_params: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Pointers to evidence (signal_ids, event_ids)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    created_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (
        Index("ix_candidate_ticker_time", "ticker", "timestamp_utc"),
        Index("ix_candidate_rule", "rule_id"),
        Index("ix_candidate_option_symbol", "option_symbol"),
    )


class ExitDecision(Base):
    """Track exit decisions triggered by exit rules."""

    __tablename__ = "exit_decisions"

    exit_id: Mapped[str] = mapped_column(String, primary_key=True)
    ticker: Mapped[str] = mapped_column(String, nullable=False, index=True)
    candidate_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)  # Links to entry

    # Exit rule info
    rule_id: Mapped[str] = mapped_column(String, nullable=False)  # Which exit rule triggered
    exit_reason: Mapped[str] = mapped_column(String, nullable=False)
    urgency: Mapped[str | None] = mapped_column(String, nullable=True)  # IMMEDIATE, SOON, CONSIDER
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Execution
    broker_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    exit_ts_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    # P&L tracking
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (Index("ix_exit_decision_exit_ts", "exit_ts_utc"),)


class StrategyDecision(Base):
    __tablename__ = "strategy_decisions"

    decision_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    candidate_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    timestamp_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Context
    ticker: Mapped[str] = mapped_column(String, nullable=False)
    strategy_version_id: Mapped[str] = mapped_column(String, nullable=False)
    model_version: Mapped[str | None] = mapped_column(String, nullable=True)

    # Decision
    decision: Mapped[str] = mapped_column(String, nullable=False)  # EXECUTE, SKIP
    p_take: Mapped[float | None] = mapped_column(Float, nullable=True)
    execution_params: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)  # Limit price logic, TIF, etc.

    reason: Mapped[str | None] = mapped_column(String, nullable=True)  # Explanation

    executed_successfully: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # "TRUE", "FALSE", "SKIPPED", "PENDING"

    # PRD 11.2 Trace
    decision_trace_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (Index("ix_strategy_decision_ticker_ts", "ticker", "timestamp_utc"),)


class GoldTickerRollup(Base):
    """
    Aggregated OHLCV bars (5m, 1h, 1d) derived from Silver Signals.
    """

    __tablename__ = "gold_ticker_rollup"

    # Composite PK: ticker + period + timestamp
    ticker: Mapped[str] = mapped_column(String, primary_key=True)
    period: Mapped[str] = mapped_column(String, primary_key=True)  # 5m, 1h, 1d
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)

    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    vwap: Mapped[float] = mapped_column(Float, nullable=False)

    created_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (Index("ix_gold_rollup_ticker_period_ts", "ticker", "period", "timestamp_utc"),)


class CandidateLabel(Base):
    """
    PRD 6.3: Triple Barrier Labels for Candidates.
    """

    __tablename__ = "candidate_labels"

    candidate_id: Mapped[str] = mapped_column(String, primary_key=True)  # FK to candidate_trades
    timestamp_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    label: Mapped[float] = mapped_column(Float, nullable=False)  # 1 (PT), -1 (SL), 0 (Time)
    ret: Mapped[float] = mapped_column(Float, nullable=False)  # Return at barrier
    barrier_hit_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # PRD 8.2 required diagnostics
    time_to_hit_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    mfe: Mapped[float | None] = mapped_column(Float, nullable=True)
    mae: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Temporal excursion fields
    ts_mfe: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ts_mae: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    time_to_mfe_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_to_mae_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    mfe_mae_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    excursion_velocity: Mapped[float | None] = mapped_column(Float, nullable=True)
    capture_efficiency: Mapped[float | None] = mapped_column(Float, nullable=True)


class LabelEvent(Base):
    """
    PRD 6.3: labels_event
    Event-level labels keyed by candidate_id, including forward returns and triple-barrier outputs.
    """

    __tablename__ = "labels_event"

    candidate_id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    ticker: Mapped[str] = mapped_column(String, nullable=False, index=True)
    event_ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    # Forward returns (configurable horizons). Keys like "1m", "5m", "60m", ...
    forward_returns: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # Triple-barrier outputs (mirror CandidateLabel)
    label: Mapped[float | None] = mapped_column(Float, nullable=True)
    ret: Mapped[float | None] = mapped_column(Float, nullable=True)
    barrier_hit_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    time_to_hit_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    mfe: Mapped[float | None] = mapped_column(Float, nullable=True)
    mae: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Temporal excursion fields
    ts_mfe: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ts_mae: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    time_to_mfe_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_to_mae_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    mfe_mae_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    excursion_velocity: Mapped[float | None] = mapped_column(Float, nullable=True)
    capture_efficiency: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Auditability: labeling parameters + provenance
    label_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ix_labels_event_ticker_ts", "ticker", "event_ts_utc"),)


class LabelWindow(Base):
    """
    PRD 6.3: labels_window
    Window-level labels keyed by (ticker, period, window_end_ts_utc).
    """

    __tablename__ = "labels_window"

    ticker: Mapped[str] = mapped_column(String, primary_key=True)
    period: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. "5m", "1h", "1d"
    window_end_ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)

    created_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    forward_returns: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    label_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (Index("ix_labels_window_ticker_period_ts", "ticker", "period", "window_end_ts_utc"),)


class GoldFeatureEvent(Base):
    """
    PRD 6.3: Point-in-time features (1m resolution or event-based).
    Persisted for model training and offline analysis.
    """

    __tablename__ = "gold_feature_events"

    # Composite PK
    ticker: Mapped[str] = mapped_column(String, primary_key=True)
    event_ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    feature_set_id: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. "v1_legacy"

    # The actual feature vector
    features: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    created_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (Index("ix_gold_feat_event_ticker_ts", "ticker", "event_ts_utc"),)
