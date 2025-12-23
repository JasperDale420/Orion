"""Add audit logs table

Revision ID: 0005_add_audit_logs
Revises: 0004_add_pgvector_embedding_vec
Create Date: 2025-12-18

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0005_add_audit_logs"
down_revision: Union[str, None] = "0004_add_pgvector_embedding_vec"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("trace_id", sa.String(), nullable=True),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("client_host", sa.String(), nullable=True),
        sa.Column("query_params", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at_utc"], unique=False)
    op.create_index("ix_audit_logs_path", "audit_logs", ["path"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_audit_logs_path", table_name="audit_logs")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
