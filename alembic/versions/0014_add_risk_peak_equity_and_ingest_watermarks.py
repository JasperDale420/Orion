"""Add risk_state peak_equity and ingest_watermarks table

Revision ID: 0014_add_risk_peak_equity_and_ingest_watermarks
Revises: 0013_extend_solver_metrics_context_fields
Create Date: 2025-12-18

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0014_add_risk_peak_equity_and_ingest_watermarks"
down_revision: Union[str, None] = "0013_extend_solver_metrics_context_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # 1) risk_state.peak_equity
    if insp.has_table("risk_state"):
        cols = {c["name"] for c in insp.get_columns("risk_state")}
        if "peak_equity" not in cols:
            op.add_column("risk_state", sa.Column("peak_equity", sa.Float(), nullable=True))
            op.execute(
                sa.text(
                    "UPDATE risk_state SET peak_equity = COALESCE(peak_equity, current_equity, starting_equity, 0.0)"
                )
            )

    # 2) ingest_watermarks table
    if not insp.has_table("ingest_watermarks"):
        op.create_table(
            "ingest_watermarks",
            sa.Column("key", sa.String(), primary_key=True),
            sa.Column("last_seen_ts_utc", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_ts_utc", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("ingest_watermarks"):
        op.drop_table("ingest_watermarks")

    if insp.has_table("risk_state"):
        cols = {c["name"] for c in insp.get_columns("risk_state")}
        if "peak_equity" in cols:
            op.drop_column("risk_state", "peak_equity")
