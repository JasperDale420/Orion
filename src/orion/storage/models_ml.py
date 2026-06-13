"""
ML Pattern Insights Database Models.

Stores weekly ML insights for pattern mining.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from orion.storage.db import Base


class MLPatternInsight(Base):
    """
    Persisted ML pattern insight from weekly mining runs.
    """

    __tablename__ = "ml_pattern_insights"

    insight_id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Model identification
    model_type: Mapped[str] = mapped_column(String, nullable=False, index=True)  # hit_target_50, avoid_stop
    model_version: Mapped[str | None] = mapped_column(String, nullable=True)

    # Training metadata
    training_window_days: Mapped[int | None] = mapped_column(Integer, default=30)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    positive_rate: Mapped[float] = mapped_column(Float, nullable=False)

    # Performance metrics
    train_auc: Mapped[float] = mapped_column(Float, nullable=False)
    holdout_auc: Mapped[float] = mapped_column(Float, nullable=False)
    precision_at_50: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Extracted patterns (JSON)
    top_rules_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    top_features_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)

    # Drift detection
    degraded_features_json: Mapped[list[Any] | None] = mapped_column(JSON, default=list)
    emerging_patterns_json: Mapped[list[Any] | None] = mapped_column(JSON, default=list)

    # Full metadata for debugging
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)


class MLFeatureImportanceHistory(Base):
    """
    Track feature importance over time for drift detection.
    """

    __tablename__ = "ml_feature_importance_history"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    model_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    feature_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    importance: Mapped[float] = mapped_column(Float, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)


class MLPrediction(Base):
    """
    Persist ML prediction and realized outcome for accuracy analytics.
    """

    __tablename__ = "ml_predictions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    prediction_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, index=True
    )

    symbol: Mapped[str] = mapped_column(String, nullable=False, index=True)
    option_chain: Mapped[str | None] = mapped_column(String, nullable=True)
    bucket: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    model_type: Mapped[str] = mapped_column(String, nullable=False, index=True)

    prediction_score: Mapped[float] = mapped_column(Float, nullable=False)
    prediction_class: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    position_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    outcome_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    hit_target: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    hit_stop: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    prediction_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
