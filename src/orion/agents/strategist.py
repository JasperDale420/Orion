import json
import logging
from typing import Any

from orion.agents.base import BaseAgent
from orion.agents.codex_client import (
    extract_json_from_response,
    run_codex_completion,
)
from orion.rag.vector_store import VectorStore
from orion.storage.models_gold import CandidateTrade

logger = logging.getLogger(__name__)


class StrategistAgent(BaseAgent):
    """
    The Strategist evaluates trading candidates using LLM reasoning
    augmented by RAG (historical context).
    """

    def __init__(self) -> None:
        from orion.config import agent_settings

        super().__init__(name="Strategist", model=agent_settings.model_name)
        self.vector_store = VectorStore()

    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
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
            "Provide a brief rationale.\n"
            "Output JSON format: {'decision': 'EXECUTE'|'SKIP', 'rationale': '...'}"
        )

        user_prompt = (
            f"Candidate Trade:\n"
            f"Ticker: {candidate.ticker}\n"
            f"Direction: {candidate.direction}\n"
            f"Rule: {candidate.rule_id}\n"
            f"Confidence: {candidate.confidence}\n"
            f"Evidence: {json.dumps(candidate.evidence)}\n\n"
            f"Context / Similar History:\n{context_str}\n\n"
            f"Make your decision."
        )

        # 3. Call AI Gateway via codex_client
        try:
            from orion.config import agent_settings

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            response = await run_codex_completion(
                messages=messages,
                model=agent_settings.model_name,
            )

            return extract_json_from_response(response)

        except Exception as e:
            logger.error(f"Strategist Agent Error: {e}")
            return {"decision": "ERROR", "rationale": str(e)}
