"""Fix embedding_vec column dimension from 1536 to 768.

The original migration (0004) created the embedding_vec column as Vector(1536),
matching OpenAI's text-embedding-ada-002 dimensions. However, the system uses
Ollama's nomic-embed-text model which produces 768-dim embeddings. This mismatch
caused all pgvector server-side searches to fail, falling back to degraded
Python-based ranking.

NOTE: This migration drops and recreates the embedding_vec column, which means
any existing pgvector embeddings will be lost. A re-index is required after
running this migration (run: python -m orion.rag.indexer).

Revision ID: 0024_fix_embedding_vec_dimension
Revises: 0023_add_ml_tracking_tables
Create Date: 2026-03-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "0024_fix_embedding_vec_dimension"
down_revision: str | None = "0023_add_ml_tracking_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pgvector does not support ALTER COLUMN to change vector dimensions,
    # so we must drop and recreate the column.
    op.drop_column("vector_documents", "embedding_vec")
    op.add_column("vector_documents", sa.Column("embedding_vec", Vector(768), nullable=True))


def downgrade() -> None:
    op.drop_column("vector_documents", "embedding_vec")
    op.add_column("vector_documents", sa.Column("embedding_vec", Vector(1536), nullable=True))
