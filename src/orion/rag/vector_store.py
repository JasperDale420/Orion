import inspect
import logging
import math
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import Float, cast, select

from orion.rag.embeddings import EmbeddingClient
from orion.shared.utils import parse_timestamptz
from orion.storage.db import async_session_factory
from orion.storage.models_rag import RagDocument

logger = logging.getLogger(__name__)


class VectorStore:
    def __init__(self) -> None:
        self.embedder = EmbeddingClient()

    async def _resolve_embedding(self, text: str) -> list[float]:
        maybe = self.embedder.get_embedding(text)
        if inspect.isawaitable(maybe):
            return await maybe
        return maybe

    async def add_document(
        self, doc_id: str, content: str, source_type: str, source_id: str, metadata: dict[str, Any]
    ) -> None:
        """
        Embeds and saves/updates a document.
        """
        embedding: list[float] = []
        embedding_vec = None
        try:
            embedding = await self._resolve_embedding(content)
            embedding_vec = embedding if len(embedding) == 1536 else None
        except Exception as e:
            logger.warning(f"Embedding unavailable; storing document without vector. doc_id={doc_id} err={e}")

        doc = RagDocument(
            doc_id=doc_id,
            content=content,
            source_type=source_type,
            source_id=source_id,
            metadata_json=metadata,
            embedding=embedding,
            embedding_vec=embedding_vec,
        )

        async with async_session_factory() as session:
            # Simple merge: Delete existing then Insert (or merge if using 2.0 style)
            # Use merge logic
            await session.merge(doc)
            await session.commit()
            logger.info(f"Indexed doc: {doc_id}")

    def _cosine_sim(self, a: Iterable[float], b: Iterable[float]) -> float:
        if a is None or b is None:
            return float("-inf")
        aa = list(a)
        bb = list(b)
        if len(aa) != len(bb) or not aa:
            return float("-inf")
        dot = sum(x * y for x, y in zip(aa, bb, strict=False))
        na = math.sqrt(sum(x * x for x in aa))
        nb = math.sqrt(sum(y * y for y in bb))
        if na == 0 or nb == 0:
            return float("-inf")
        return dot / (na * nb)

    async def search(
        self,
        query: str,
        k: int = 10,
        ticker: str | None = None,
        tickers: list[str] | None = None,
        doc_type: str | None = None,
        rule_id: str | None = None,
        model_version: str | None = None,
        market_session: str | None = None,
        start: str | None = None,
        end: str | None = None,
        min_premium_usd: float | None = None,
        max_premium_usd: float | None = None,
    ) -> list[RagDocument]:
        """Semantic search with optional metadata filters."""
        query_embedding: list[float] | None = None
        try:
            query_embedding = await self._resolve_embedding(query)
        except Exception as e:
            logger.warning(f"Embedding unavailable for query; falling back to keyword-only search: {e}")

        start_dt: datetime | None = None
        end_dt: datetime | None = None
        if start:
            start_dt = parse_timestamptz(start, strict=True)
        if end:
            end_dt = parse_timestamptz(end, strict=True)

        async def execute_search(db: Any) -> list[RagDocument]:
            stmt_base = select(RagDocument)
            if tickers:
                normalized = [t.strip() for t in tickers if t and t.strip()]
                if normalized:
                    stmt_base = stmt_base.where(RagDocument.metadata_json["ticker"].as_string().in_(normalized))
            elif ticker:
                stmt_base = stmt_base.where(RagDocument.metadata_json["ticker"].as_string() == ticker)
            if doc_type:
                stmt_base = stmt_base.where(
                    (RagDocument.metadata_json["doc_type"].as_string() == doc_type)
                    | (RagDocument.metadata_json["entity"].as_string() == doc_type)
                )
            if rule_id:
                stmt_base = stmt_base.where(RagDocument.metadata_json["rule_id"].as_string() == rule_id)
            if model_version:
                stmt_base = stmt_base.where(RagDocument.metadata_json["model_version"].as_string() == model_version)
            if market_session:
                stmt_base = stmt_base.where(RagDocument.metadata_json["session"].as_string() == market_session)
            if start_dt:
                stmt_base = stmt_base.where(RagDocument.metadata_json["timestamp"].as_string() >= start_dt.isoformat())
            if end_dt:
                stmt_base = stmt_base.where(RagDocument.metadata_json["timestamp"].as_string() < end_dt.isoformat())
            if min_premium_usd is not None:
                stmt_base = stmt_base.where(
                    cast(RagDocument.metadata_json["premium_usd"].as_string(), Float) >= float(min_premium_usd)
                )
            if max_premium_usd is not None:
                stmt_base = stmt_base.where(
                    cast(RagDocument.metadata_json["premium_usd"].as_string(), Float) <= float(max_premium_usd)
                )

            # Prefer server-side vector search if available; otherwise fallback to in-python ranking.
            vec_docs: list[RagDocument] = []
            try:
                if not query_embedding:
                    raise ValueError("No query embedding available")
                if len(query_embedding) != 1536:
                    raise ValueError(f"Unexpected embedding dimension {len(query_embedding)} (expected 1536)")
                stmt_vec = (
                    stmt_base.where(RagDocument.embedding_vec.is_not(None))
                    .order_by(RagDocument.embedding_vec.l2_distance(query_embedding))
                    .limit(max(200, k))
                )
                result = await db.execute(stmt_vec)
                vec_docs = list(result.scalars().all() or [])
            except Exception as e:
                logger.warning(f"Server-side vector search unavailable, falling back to python rank: {e}")

            # Keyword fallback (PRD hybrid). This is a simple baseline and can be replaced with tsvector/GIN later.
            stmt_kw = (
                stmt_base.where(RagDocument.content.ilike(f"%{query}%"))
                .order_by(RagDocument.created_at_utc.desc())
                .limit(200)
            )
            result_kw = await db.execute(stmt_kw)
            kw_docs = list(result_kw.scalars().all() or [])

            merged: dict[str, RagDocument] = {d.doc_id: d for d in vec_docs}
            for d in kw_docs:
                merged.setdefault(d.doc_id, d)

            docs = list(merged.values())
            if vec_docs and query_embedding:
                docs = sorted(docs, key=lambda d: self._cosine_sim(d.embedding, query_embedding), reverse=True)
            return docs[:k]

        async with async_session_factory() as session:
            return await execute_search(session)
