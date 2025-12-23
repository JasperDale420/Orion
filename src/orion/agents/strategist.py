import json
import logging
import os
from typing import Any, Dict

from openai import AsyncOpenAI
from orion.agents.base import BaseAgent
from orion.rag.vector_store import VectorStore
from orion.storage.models_gold import CandidateTrade

logger = logging.getLogger(__name__)


class StrategistAgent(BaseAgent):
    """
    The Strategist evaluates trading candidates using LLM reasoning
    augmented by RAG (historical context).
    """

    def __init__(self):
        super().__init__(name="Strategist", model="gpt-4-turbo")  # Or gpt-3.5-turbo if prefer cheaper
        api_key = os.getenv("OPENAI_API_KEY")
        self.client = AsyncOpenAI(api_key=api_key)
        self.vector_store = VectorStore()

    async def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        candidate: CandidateTrade = context.get("candidate")
        if not candidate:
            return {"error": "No candidate provided"}

        # 1. RAG Lookup: Find similar past trades or relevant context
        query = f"{candidate.direction} on {candidate.ticker} with {candidate.rule_id}"
        related_docs = await self.vector_store.search(query, k=3)

        context_str = "\n".join([f"- {d.content}" for d in related_docs])

        # 2. Construct Prompt
        system_prompt = (
            "You are a Senior Quantitative Trader. Your job is to evaluate trading signals.\n"
            "You will be given a Candidate Trade and some context (RAG).\n"
            "Analyze the signal. If the signal is strong, output decision: EXECUTE.\n"
            "If weak or risky, output decision: SKIP.\n"
            "Provide a brief rationale."
        )

        user_prompt = (
            f"Candidate Trade:\n"
            f"Ticker: {candidate.ticker}\n"
            f"Direction: {candidate.direction}\n"
            f"Rule: {candidate.rule_id}\n"
            f"Confidence: {candidate.confidence}\n"
            f"Evidence: {json.dumps(candidate.evidence)}\n\n"
            f"Context / Similar History:\n{context_str}\n\n"
            f"Make your decision. JSON format: {{'decision': 'EXECUTE'|'SKIP', 'rationale': '...'}}"
        )

        # 3. Call LLM
        try:
            response = await self.client.chat.completions.create(
                model="gpt-4-turbo",
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            decision_json = json.loads(content)

            return decision_json

        except Exception as e:
            logger.error(f"Strategist Agent Error: {e}")
            return {"decision": "ERROR", "rationale": str(e)}
