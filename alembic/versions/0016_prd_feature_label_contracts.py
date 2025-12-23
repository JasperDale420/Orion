"""PRD feature/label contracts: labels_event/labels_window + feature store views

Revision ID: 0016_prd_feature_label_contracts
Revises: 0015_extend_solvers_prdv2_fields
Create Date: 2025-12-18
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0016_prd_feature_label_contracts"
down_revision: Union[str, None] = "0015_extend_solvers_prdv2_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("labels_event"):
        op.create_table(
            "labels_event",
            sa.Column("candidate_id", sa.String(), primary_key=True),
            sa.Column("created_at_utc", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("ticker", sa.String(), nullable=False),
            sa.Column("event_ts_utc", sa.DateTime(timezone=True), nullable=False),
            sa.Column("forward_returns", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("label", sa.Float(), nullable=True),
            sa.Column("ret", sa.Float(), nullable=True),
            sa.Column("barrier_hit_ts", sa.DateTime(timezone=True), nullable=True),
            sa.Column("time_to_hit_seconds", sa.Float(), nullable=True),
            sa.Column("mfe", sa.Float(), nullable=True),
            sa.Column("mae", sa.Float(), nullable=True),
            sa.Column("label_config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        )
        op.create_index("ix_labels_event_ticker_ts", "labels_event", ["ticker", "event_ts_utc"], unique=False)

    if not insp.has_table("labels_window"):
        op.create_table(
            "labels_window",
            sa.Column("ticker", sa.String(), primary_key=True),
            sa.Column("period", sa.String(), primary_key=True),
            sa.Column("window_end_ts_utc", sa.DateTime(timezone=True), primary_key=True),
            sa.Column("created_at_utc", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("forward_returns", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("label_config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        )
        op.create_index(
            "ix_labels_window_ticker_period_ts",
            "labels_window",
            ["ticker", "period", "window_end_ts_utc"],
            unique=False,
        )

    # Transitional PRD feature store names:
    # - features_event → gold_feature_events
    # - features_window → gold_feature_windows
    # Use SQL views for compatibility; safe for sqlite/postgres.
    if insp.has_table("gold_feature_events"):
        op.execute(sa.text("CREATE VIEW IF NOT EXISTS features_event AS " "SELECT * FROM gold_feature_events"))
    if insp.has_table("gold_feature_windows"):
        op.execute(sa.text("CREATE VIEW IF NOT EXISTS features_window AS " "SELECT * FROM gold_feature_windows"))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # Drop views first (if present)
    try:
        op.execute(sa.text("DROP VIEW IF EXISTS features_event"))
    except Exception:
        pass
    try:
        op.execute(sa.text("DROP VIEW IF EXISTS features_window"))
    except Exception:
        pass

    if insp.has_table("labels_window"):
        op.drop_index("ix_labels_window_ticker_period_ts", table_name="labels_window")
        op.drop_table("labels_window")
    if insp.has_table("labels_event"):
        op.drop_index("ix_labels_event_ticker_ts", table_name="labels_event")
        op.drop_table("labels_event")
