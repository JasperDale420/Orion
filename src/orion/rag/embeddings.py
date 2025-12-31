"""
Embedding client that supports both OpenAI and local Ollama inference.

Uses Ollama by default for local inference, falls back to OpenAI if configured.
"""

import logging
import os
from typing import List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# Ollama embedding model - nomic-embed-text is purpose-built for RAG
OLLAMA_EMBEDDING_MODEL = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")


class EmbeddingClient:
    """
    Embedding client supporting Ollama (preferred) and OpenAI.
    
    Priority:
    1. Ollama (local) - if available
    2. OpenAI - if OPENAI_API_KEY is set
    """

    def __init__(self) -> None:
        self.use_ollama = True
        self.ollama_model = OLLAMA_EMBEDDING_MODEL
        self.ollama_url = f"{OLLAMA_BASE_URL}/api/embeddings"
        
        # Fallback to OpenAI if configured
        self.openai_client: Optional["AsyncOpenAI"] = None
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key and not api_key.startswith("sk-your"):
            try:
                from openai import AsyncOpenAI
                self.openai_client = AsyncOpenAI(api_key=api_key)
            except ImportError:
                pass
        
        logger.info(
            f"EmbeddingClient initialized: ollama={self.use_ollama}, "
            f"model={self.ollama_model}, openai_fallback={self.openai_client is not None}"
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def get_embedding(self, text: str) -> List[float]:
        """
        Get embedding for text using Ollama (preferred) or OpenAI.
        """
        text = text.replace("\n", " ").strip()
        
        if self.use_ollama:
            try:
                return await self._get_ollama_embedding(text)
            except Exception as e:
                logger.warning(f"Ollama embedding failed: {e}, trying OpenAI fallback")
                if self.openai_client:
                    return await self._get_openai_embedding(text)
                raise
        
        if self.openai_client:
            return await self._get_openai_embedding(text)
        
        raise RuntimeError("No embedding backend available (Ollama or OpenAI)")

    async def _get_ollama_embedding(self, text: str) -> List[float]:
        """Get embedding from local Ollama."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                self.ollama_url,
                json={"model": self.ollama_model, "prompt": text},
            )
            response.raise_for_status()
            data = response.json()
            embedding = data.get("embedding")
            if not embedding:
                raise RuntimeError(f"No embedding in Ollama response: {data}")
            return embedding

    async def _get_openai_embedding(self, text: str) -> List[float]:
        """Get embedding from OpenAI API."""
        if not self.openai_client:
            raise RuntimeError("OpenAI client not configured")
        response = await self.openai_client.embeddings.create(
            input=[text],
            model="text-embedding-3-small",
        )
        return response.data[0].embedding
