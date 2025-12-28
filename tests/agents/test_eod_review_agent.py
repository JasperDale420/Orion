from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orion.agents.eod_review_agent import EODReviewAgent


@pytest.mark.asyncio
async def test_eod_agent_run_review_smoke():
    """
    Smoke test to ensure EODReviewAgent.run_review executes without error.
    Mocks external dependencies (LLM, DB, FS).
    """
    # Mock LLM
    mock_llm = AsyncMock()
    mock_llm.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content='{"analysis": "Mock Report", "proposals": []}'))
    ]

    agent = EODReviewAgent(llm_client=mock_llm)

    # Mock dependencies
    with patch.object(agent, "_gather_data", return_value=({"mock": "data"}, "mock_snapshot.json")), patch.object(
        agent, "_fetch_rag_context", return_value="Mock Context"
    ), patch.object(agent, "_persist_solver_edits", new_callable=AsyncMock), patch(
        "builtins.open", new_callable=MagicMock
    ), patch(
        "os.makedirs"
    ):
        # Execute
        result = await agent.run_review()

        # Assertions
        assert "run_id" in result
        assert result["proposals_count"] == 0
        assert mock_llm.chat.completions.create.called
