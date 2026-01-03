from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Column, DateTime, String, Text

from orion.storage.db import Base


class VectorDocument(Base):
    __tablename__ = "vector_documents"

    # Unique ID (can be derived from source ID)
    doc_id = Column(String, primary_key=True)

    # Source identifiers
    source_type = Column(String, nullable=False)  # e.g. "CANDIDATE_TRADE", "NEWS", "PRD"
    source_id = Column(String, nullable=False)

    # Text content and Embedding
    content = Column(Text, nullable=False)
    embedding = Column(JSON, nullable=False)  # raw embedding list (portable)
    embedding_vec = Column(Vector(768), nullable=True)  # server-side pgvector (768 for nomic-embed-text)

    # Metadata for filtering
    metadata_json = Column(JSON, nullable=False, default={})

    created_at_utc = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# Alias for legacy imports
RagDocument = VectorDocument
