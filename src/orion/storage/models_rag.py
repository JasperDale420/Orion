from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from orion.storage.db import Base


class VectorDocument(Base):
    __tablename__ = "vector_documents"

    # Unique ID (can be derived from source ID)
    doc_id: Mapped[str] = mapped_column(String, primary_key=True)

    # Source identifiers
    source_type: Mapped[str] = mapped_column(String, nullable=False)  # e.g. "CANDIDATE_TRADE", "NEWS", "PRD"
    source_id: Mapped[str] = mapped_column(String, nullable=False)

    # Text content and Embedding
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[Any]] = mapped_column(JSON, nullable=False)  # raw embedding list (portable)
    # server-side pgvector (768 for nomic-embed-text). Mapped[Any]: the Python-side
    # value is a list/ndarray depending on driver, so an honest annotation is Any.
    embedding_vec: Mapped[Any] = mapped_column(Vector(768), nullable=True)

    # Metadata for filtering
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


# Alias for legacy imports
RagDocument = VectorDocument
