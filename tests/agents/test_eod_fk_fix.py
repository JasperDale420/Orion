"""Test for foreign key validation fix in EOD review agent."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.agents.eod_review_agent import EODReviewAgent
from orion.storage.models_solvers import Solver, SolverEdits


@pytest.mark.asyncio
async def test_eod_agent_skips_solver_creation_when_parent_missing():
    """
    Test that EOD agent gracefully handles missing parent solver.

    This test validates the fix for the FK violation when parent_solver_id
    references a non-existent solver.
    """
    # Mock LLM
    mock_llm = AsyncMock()
    mock_llm.chat.completions.create.return_value.choices = [
        MagicMock(
            message=MagicMock(
                content='{"analysis": "Mock Report", "proposals": [{"type": "solver_edit", "target_solver_id": "nonexistent_solver", "ops": [{"op": "modify_param", "param_name": "test", "new_value": 1}]}]}'
            )
        )
    ]

    agent = EODReviewAgent(llm_client=mock_llm)

    # Mock session with missing parent solver
    mock_session = AsyncMock()

    # Create a proper mock result chain for execute
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = None
    mock_session.execute.return_value = mock_result

    mock_session.get.return_value = None  # Parent solver doesn't exist

    async def mock_db_write(func):
        await func(mock_session)

    with (
        patch.object(agent, "_gather_data", return_value=({"mock": "data"}, "mock_snapshot.json")),
        patch.object(agent, "_fetch_rag_context", return_value="Mock Context"),
        patch("orion.agents.eod_review_agent.db_write", side_effect=mock_db_write),
        patch("builtins.open", new_callable=MagicMock),
        patch("os.makedirs"),
    ):
        # Execute - should not raise FK violation
        result = await agent.run_review()

        # Verify the session.get was called to check parent solver
        mock_session.get.assert_called()

        # Verify no Solver was added (since parent doesn't exist)
        # But SolverEdits should still be added
        solver_adds = [call for call in mock_session.add.call_args_list if isinstance(call[0][0], Solver)]
        edit_adds = [call for call in mock_session.add.call_args_list if isinstance(call[0][0], SolverEdits)]

        # Should have no Solver adds (parent missing) but should have SolverEdits
        assert len(solver_adds) == 0
        assert len(edit_adds) == 1

        assert result["proposals_count"] == 1
