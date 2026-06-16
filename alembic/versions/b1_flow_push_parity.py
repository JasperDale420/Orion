"""flow_push_parity

Shadow-mode parity table (redesign R1 / task B1). One row per ingestion cycle
recording how the Gateway WS push flow set compares to the Heber-Silver poll
set (event-id parity), the metric that gates the C4 cutover to push.

Hand-written post-baseline migration. ``down_revision`` chains off the A1
liveness migration so ``alembic heads`` stays single (B3 chains onto this id).
After this lands, ``alembic revision --autogenerate`` against the model must
show no residual diff (guarded by tests/storage/test_flow_push_parity_migration.py).

Revision ID: b1_flow_push_parity
Revises: a1_service_liveness
Create Date: 2026-06-11
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1_flow_push_parity"
down_revision: Union[str, Sequence[str], None] = "a1_service_liveness"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "flow_push_parity",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("cycle_ts_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("push_count", sa.Integer(), nullable=False),
        sa.Column("poll_count", sa.Integer(), nullable=False),
        sa.Column("matched_count", sa.Integer(), nullable=False),
        sa.Column("missed_by_push_count", sa.Integer(), nullable=False),
        sa.Column("missed_by_poll_count", sa.Integer(), nullable=False),
        sa.Column("parity_unmatchable_count", sa.Integer(), nullable=False),
        sa.Column("median_latency_improvement_s", sa.Float(), nullable=True),
        sa.Column("window_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missed_by_push_ids", sa.JSON(), nullable=True),
        sa.Column("trace_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_flow_push_parity_cycle_ts_utc"),
        "flow_push_parity",
        ["cycle_ts_utc"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_flow_push_parity_cycle_ts_utc"), table_name="flow_push_parity")
    op.drop_table("flow_push_parity")
