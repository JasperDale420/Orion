import logging
import os
from typing import List

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class EmbeddingClient:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not found. Vector embeddings will be disabled (keyword-only search).")
            self.client = None
        else:
            self.client = AsyncOpenAI(api_key=api_key)
        self.model = "text-embedding-3-small"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def get_embedding(self, text: str) -> List[float]:
        if self.client is None:
            raise RuntimeError("Embeddings client not configured (missing OPENAI_API_KEY)")
        try:
            # Replace newlines
            text = text.replace("\n", " ")
            response = await self.client.embeddings.create(input=[text], model=self.model)
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            raise
