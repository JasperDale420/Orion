"""
TradingRAG Client.

Client for querying the TradingRAG service for trading strategy research.
Provides access to indexed trading books for context-aware Q&A.
"""

import os
from inspect import isawaitable
from typing import Any, Dict, List, Optional

import httpx

from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.clients.trading_rag")

# Configuration
TRADING_RAG_URL = os.getenv("TRADING_RAG_URL", "http://localhost:8005")
TRADING_RAG_API_KEY = os.getenv("TRADING_RAG_API_KEY", "")


class TradingRAGClient:
    """
    Client for TradingRAG service.

    Provides semantic search and Q&A over indexed trading books.
    """

    def __init__(
        self,
        base_url: str = TRADING_RAG_URL,
        api_key: str = TRADING_RAG_API_KEY,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"X-API-Key": self.api_key},
                timeout=self.timeout,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant document chunks for a query.

        Args:
            query: Search query
            top_k: Number of results to return

        Returns:
            List of document chunks with scores
        """
        client = await self._get_client()

        try:
            response = await client.post(
                "/retrieve",
                json={"query": query, "top_k": top_k},
            )
            maybe_result = response.raise_for_status()
            if isawaitable(maybe_result):
                await maybe_result
            data = response.json()

            logger.info(
                f"Retrieved {len(data.get('results', []))} chunks for query",
                extra={"event": "rag_retrieve", "query": query[:50]},
            )
            return data.get("results", [])
        except Exception as e:
            logger.warning(f"TradingRAG retrieve failed: {e}")
            return []

    async def answer(
        self,
        query: str,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """
        Get an LLM-generated answer based on retrieved context.

        Args:
            query: Question to answer
            top_k: Number of context chunks to use

        Returns:
            Dict with answer and sources
        """
        client = await self._get_client()

        try:
            response = await client.post(
                "/answer",
                json={"query": query, "top_k": top_k},
            )
            maybe_result = response.raise_for_status()
            if isawaitable(maybe_result):
                await maybe_result
            data = response.json()

            logger.info(
                "Got answer for query",
                extra={"event": "rag_answer", "query": query[:50]},
            )
            return {
                "answer": data.get("answer", ""),
                "sources": data.get("sources", []),
            }
        except Exception as e:
            logger.warning(f"TradingRAG answer failed: {e}")
            return {"answer": "", "sources": []}

    async def ask_strategy_question(self, question: str) -> str:
        """
        Convenience method for strategy research.

        Returns just the answer text.
        """
        result = await self.answer(question)
        return result.get("answer", "No answer available")


# Singleton instance
_rag_client: Optional[TradingRAGClient] = None


def get_rag_client() -> TradingRAGClient:
    """Get or create TradingRAG client singleton."""
    global _rag_client
    if _rag_client is None:
        _rag_client = TradingRAGClient()
    return _rag_client
