"""service_liveness

Unified liveness-contract table (redesign R3 / task RA.1). One row per
long-running Orion service; each service upserts at the end of every successful
work cycle. The dead-man watchdog reads these rows and alerts on absence.

Hand-written post-baseline migration. ``down_revision`` is the squashed
baseline, NOT one of the archived incremental migrations. After this lands,
``alembic revision --autogenerate`` against the model must show no residual
diff (verified by tests/storage/test_baseline_parity.py extending its import
set to include models_liveness).

Revision ID: a1_service_liveness
Revises: baseline_2026_06_11
Create Date: 2026-06-11
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1_service_liveness"
down_revision: Union[str, Sequence[str], None] = "baseline_2026_06_11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "service_liveness",
        sa.Column("service", sa.String(), nullable=False),
        sa.Column("last_success_ts_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cycle_count", sa.BigInteger(), nullable=False),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("cadence_budget_seconds", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("service"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("service_liveness")
