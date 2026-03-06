"""Meta-layer PRD fields: extend meta_experiments + solver_edits

Revision ID: 0018_meta_experiments_prd_fields
Revises: 0017_extend_dlq_envelope_fields
Create Date: 2025-12-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_meta_experiments_prd_fields"
down_revision: str | None = "0017_extend_dlq_envelope_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # meta_experiments table may not exist if DB was created via create_all; ensure it exists.
    if not insp.has_table("meta_experiments"):
        op.create_table(
            "meta_experiments",
            sa.Column("experiment_id", sa.String(), primary_key=True),
            sa.Column("description", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="running"),
            sa.Column("start_time_utc", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("end_time_utc", sa.DateTime(timezone=True), nullable=True),
            sa.Column("trial_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("best_solver_id", sa.String(), nullable=True),
        )
        insp = sa.inspect(bind)

    cols = {c["name"] for c in insp.get_columns("meta_experiments")}

    def add_col(name: str, col: sa.Column) -> None:
        if name not in cols:
            op.add_column("meta_experiments", col)

    add_col("id", sa.Column("id", sa.String(), nullable=True))
    add_col("name", sa.Column("name", sa.String(), nullable=True))
    add_col("objective", sa.Column("objective", sa.String(), nullable=True))
    add_col("base_solver_ids", sa.Column("base_solver_ids", sa.JSON(), nullable=True))
    add_col("config_json", sa.Column("config_json", sa.JSON(), nullable=True))
    add_col("summary", sa.Column("summary", sa.String(), nullable=True))
    add_col("started_at", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    add_col("completed_at", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))

    # Backfill best-effort
    try:
        op.execute(sa.text("UPDATE meta_experiments SET id = COALESCE(id, experiment_id)"))
        op.execute(sa.text("UPDATE meta_experiments SET name = COALESCE(name, description)"))
        op.execute(sa.text("UPDATE meta_experiments SET started_at = COALESCE(started_at, start_time_utc)"))
        op.execute(sa.text("UPDATE meta_experiments SET completed_at = COALESCE(completed_at, end_time_utc)"))
    except Exception:
        pass

    # solver_edits: add evaluated_at_utc (PRD 4.5 evaluated_at)
    if insp.has_table("solver_edits"):
        cols_e = {c["name"] for c in insp.get_columns("solver_edits")}
        if "evaluated_at_utc" not in cols_e:
            op.add_column("solver_edits", sa.Column("evaluated_at_utc", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("solver_edits"):
        cols_e = {c["name"] for c in insp.get_columns("solver_edits")}
        if "evaluated_at_utc" in cols_e:
            op.drop_column("solver_edits", "evaluated_at_utc")

    if insp.has_table("meta_experiments"):
        cols = {c["name"] for c in insp.get_columns("meta_experiments")}
        for name in [
            "completed_at",
            "started_at",
            "summary",
            "config_json",
            "base_solver_ids",
            "objective",
            "name",
            "id",
        ]:
            if name in cols:
                op.drop_column("meta_experiments", name)
