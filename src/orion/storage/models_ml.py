"""
ML Pattern Insights Database Models.

Stores weekly ML insights for pattern mining.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Integer, String

from orion.storage.db import Base


class MLPatternInsight(Base):
    """
    Persisted ML pattern insight from weekly mining runs.
    """

    __tablename__ = "ml_pattern_insights"

    insight_id = Column(String, primary_key=True)
    created_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Model identification
    model_type = Column(String, nullable=False, index=True)  # hit_target_50, avoid_stop
    model_version = Column(String, nullable=True)

    # Training metadata
    training_window_days = Column(Integer, default=30)
    sample_size = Column(Integer, nullable=False)
    positive_rate = Column(Float, nullable=False)

    # Performance metrics
    train_auc = Column(Float, nullable=False)
    holdout_auc = Column(Float, nullable=False)
    precision_at_50 = Column(Float, nullable=True)

    # Extracted patterns (JSON)
    top_rules_json = Column(JSON, nullable=False, default=list)
    top_features_json = Column(JSON, nullable=False, default=list)

    # Drift detection
    degraded_features_json = Column(JSON, default=list)
    emerging_patterns_json = Column(JSON, default=list)

    # Full metadata for debugging
    metadata_json = Column(JSON, default=dict)


class MLFeatureImportanceHistory(Base):
    """
    Track feature importance over time for drift detection.
    """

    __tablename__ = "ml_feature_importance_history"

    id = Column(String, primary_key=True)
    created_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    model_type = Column(String, nullable=False, index=True)
    feature_name = Column(String, nullable=False, index=True)
    importance = Column(Float, nullable=False)
    rank = Column(Integer, nullable=False)


class MLPrediction(Base):
    """
    Persist ML prediction and realized outcome for accuracy analytics.
    """

    __tablename__ = "ml_predictions"

    id = Column(String, primary_key=True)
    prediction_ts = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, index=True)

    symbol = Column(String, nullable=False, index=True)
    option_chain = Column(String, nullable=True)
    bucket = Column(String, nullable=True, index=True)
    model_type = Column(String, nullable=False, index=True)

    prediction_score = Column(Float, nullable=False)
    prediction_class = Column(Integer, nullable=False)
    confidence = Column(Float, nullable=True)

    position_id = Column(String, nullable=True, index=True)
    outcome_ts = Column(DateTime(timezone=True), nullable=True)
    actual_return_pct = Column(Float, nullable=True)
    hit_target = Column(Boolean, nullable=True)
    hit_stop = Column(Boolean, nullable=True)
    prediction_correct = Column(Boolean, nullable=True)
