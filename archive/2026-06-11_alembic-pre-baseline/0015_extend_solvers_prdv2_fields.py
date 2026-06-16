"""Extend solvers table with PRDv2 fields (staged)

Revision ID: 0015_extend_solvers_prdv2_fields
Revises: 0014_add_risk_peak_equity_and_ingest_watermarks
Create Date: 2025-12-18

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_extend_solvers_prdv2_fields"
down_revision: str | None = "0014_add_risk_peak_equity_and_ingest_watermarks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("solvers"):
        return

    cols = {c["name"] for c in insp.get_columns("solvers")}

    def add_col(name: str, col: sa.Column) -> None:
        if name not in cols:
            op.add_column("solvers", col)

    add_col("name", sa.Column("name", sa.String(), nullable=True))
    add_col("version", sa.Column("version", sa.Integer(), nullable=True))
    add_col("status", sa.Column("status", sa.String(), nullable=True))
    add_col("parent_solver_id", sa.Column("parent_solver_id", sa.String(), nullable=True))
    add_col("created_by", sa.Column("created_by", sa.String(), nullable=True))
    add_col("notes", sa.Column("notes", sa.String(), nullable=True))
    add_col("definition_json", sa.Column("definition_json", sa.JSON(), nullable=True))

    # Backfills (best-effort; safe on sqlite/postgres)
    op.execute(sa.text("UPDATE solvers SET name = COALESCE(name, family_name)"))
    op.execute(sa.text("UPDATE solvers SET version = COALESCE(version, 1)"))
    op.execute(
        sa.text("UPDATE solvers SET status = COALESCE(status, CASE WHEN is_active THEN 'active' ELSE 'candidate' END)")
    )
    op.execute(sa.text("UPDATE solvers SET created_by = COALESCE(created_by, 'human')"))
    op.execute(sa.text("UPDATE solvers SET definition_json = COALESCE(definition_json, config)"))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("solvers"):
        return

    cols = {c["name"] for c in insp.get_columns("solvers")}
    for name in ["definition_json", "notes", "created_by", "parent_solver_id", "status", "version", "name"]:
        if name in cols:
            op.drop_column("solvers", name)
