"""Add ML tracking tables for pattern insights and prediction outcomes.

Revision ID: 0023_add_ml_tracking_tables
Revises: d945e89a1b2c
Create Date: 2026-02-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023_add_ml_tracking_tables"
down_revision: Union[str, None] = "d945e89a1b2c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return insp.has_table(table_name)


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    try:
        indexes = insp.get_indexes(table_name)
    except Exception:
        return False
    return any(idx.get("name") == index_name for idx in indexes)


def upgrade() -> None:
    if not _has_table("ml_pattern_insights"):
        op.create_table(
            "ml_pattern_insights",
            sa.Column("insight_id", sa.String(), primary_key=True),
            sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("model_type", sa.String(), nullable=False),
            sa.Column("model_version", sa.String(), nullable=True),
            sa.Column("training_window_days", sa.Integer(), nullable=True, server_default="30"),
            sa.Column("sample_size", sa.Integer(), nullable=False),
            sa.Column("positive_rate", sa.Float(), nullable=False),
            sa.Column("train_auc", sa.Float(), nullable=False),
            sa.Column("holdout_auc", sa.Float(), nullable=False),
            sa.Column("precision_at_50", sa.Float(), nullable=True),
            sa.Column("top_rules_json", sa.JSON(), nullable=False),
            sa.Column("top_features_json", sa.JSON(), nullable=False),
            sa.Column("degraded_features_json", sa.JSON(), nullable=True),
            sa.Column("emerging_patterns_json", sa.JSON(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
        )

    if not _has_index("ml_pattern_insights", "ix_ml_pattern_insights_model_type"):
        op.create_index("ix_ml_pattern_insights_model_type", "ml_pattern_insights", ["model_type"], unique=False)

    if not _has_table("ml_feature_importance_history"):
        op.create_table(
            "ml_feature_importance_history",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("model_type", sa.String(), nullable=False),
            sa.Column("feature_name", sa.String(), nullable=False),
            sa.Column("importance", sa.Float(), nullable=False),
            sa.Column("rank", sa.Integer(), nullable=False),
        )

    if not _has_index("ml_feature_importance_history", "ix_ml_feature_importance_history_model_type"):
        op.create_index(
            "ix_ml_feature_importance_history_model_type",
            "ml_feature_importance_history",
            ["model_type"],
            unique=False,
        )
    if not _has_index("ml_feature_importance_history", "ix_ml_feature_importance_history_feature_name"):
        op.create_index(
            "ix_ml_feature_importance_history_feature_name",
            "ml_feature_importance_history",
            ["feature_name"],
            unique=False,
        )

    if not _has_table("ml_predictions"):
        op.create_table(
            "ml_predictions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("prediction_ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("symbol", sa.String(), nullable=False),
            sa.Column("option_chain", sa.String(), nullable=True),
            sa.Column("bucket", sa.String(), nullable=True),
            sa.Column("model_type", sa.String(), nullable=False),
            sa.Column("prediction_score", sa.Float(), nullable=False),
            sa.Column("prediction_class", sa.Integer(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("position_id", sa.String(), nullable=True),
            sa.Column("outcome_ts", sa.DateTime(timezone=True), nullable=True),
            sa.Column("actual_return_pct", sa.Float(), nullable=True),
            sa.Column("hit_target", sa.Boolean(), nullable=True),
            sa.Column("hit_stop", sa.Boolean(), nullable=True),
            sa.Column("prediction_correct", sa.Boolean(), nullable=True),
        )

    for idx_name, cols in [
        ("ix_ml_predictions_prediction_ts", ["prediction_ts"]),
        ("ix_ml_predictions_symbol", ["symbol"]),
        ("ix_ml_predictions_bucket", ["bucket"]),
        ("ix_ml_predictions_model_type", ["model_type"]),
        ("ix_ml_predictions_position_id", ["position_id"]),
    ]:
        if not _has_index("ml_predictions", idx_name):
            op.create_index(idx_name, "ml_predictions", cols, unique=False)


def downgrade() -> None:
    # Drop indexes first if present.
    for table_name, idx_name in [
        ("ml_predictions", "ix_ml_predictions_position_id"),
        ("ml_predictions", "ix_ml_predictions_model_type"),
        ("ml_predictions", "ix_ml_predictions_bucket"),
        ("ml_predictions", "ix_ml_predictions_symbol"),
        ("ml_predictions", "ix_ml_predictions_prediction_ts"),
        ("ml_feature_importance_history", "ix_ml_feature_importance_history_feature_name"),
        ("ml_feature_importance_history", "ix_ml_feature_importance_history_model_type"),
        ("ml_pattern_insights", "ix_ml_pattern_insights_model_type"),
    ]:
        if _has_table(table_name) and _has_index(table_name, idx_name):
            op.drop_index(idx_name, table_name=table_name)

    if _has_table("ml_predictions"):
        op.drop_table("ml_predictions")
    if _has_table("ml_feature_importance_history"):
        op.drop_table("ml_feature_importance_history")
    if _has_table("ml_pattern_insights"):
        op.drop_table("ml_pattern_insights")
