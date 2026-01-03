"""
ML Pattern Insights Database Models.

Stores weekly ML insights for pattern mining.
"""

from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Float, Integer, String

from orion.storage.db import Base


class MLPatternInsight(Base):
    """
    Persisted ML pattern insight from weekly mining runs.
    """

    __tablename__ = "ml_pattern_insights"

    insight_id = Column(String, primary_key=True)
    created_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

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
    created_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    model_type = Column(String, nullable=False, index=True)
    feature_name = Column(String, nullable=False, index=True)
    importance = Column(Float, nullable=False)
    rank = Column(Integer, nullable=False)
