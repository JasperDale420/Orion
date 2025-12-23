"""Add pgvector embedding_vec column

Revision ID: 0004_add_pgvector_embedding_vec
Revises: 0003_add_silver_uw_alerts
Create Date: 2025-12-18

"""

from typing import Sequence, Union

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "0004_add_pgvector_embedding_vec"
down_revision: Union[str, None] = "0003_add_silver_uw_alerts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.add_column("vector_documents", sa.Column("embedding_vec", Vector(1536), nullable=True))
    op.create_index("ix_vector_documents_source_type", "vector_documents", ["source_type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_vector_documents_source_type", table_name="vector_documents")
    op.drop_column("vector_documents", "embedding_vec")
