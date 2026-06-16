"""Add server_default gen_random_uuid() to decision_id primary keys.

Prevents NOT NULL constraint failures on trade_journal_entries and
strategy_decisions when the ORM-level Python default does not fire
(e.g., merge conflicts, detached objects, raw SQL inserts).

Revision ID: 0027_add_server_default_uuid_to_decision_id_pks
Revises: 0026_seed_initial_solvers
Create Date: 2026-03-25

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027_add_server_default_uuid_to_decision_id_pks"
down_revision: str | None = "0026_seed_initial_solvers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.alter_column(
        "trade_journal_entries",
        "decision_id",
        server_default=sa.text("gen_random_uuid()::text"),
    )
    op.alter_column(
        "strategy_decisions",
        "decision_id",
        server_default=sa.text("gen_random_uuid()::text"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.alter_column(
        "strategy_decisions",
        "decision_id",
        server_default=None,
    )
    op.alter_column(
        "trade_journal_entries",
        "decision_id",
        server_default=None,
    )
