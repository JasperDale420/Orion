import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock dependencies that might be missing in test env
sys.modules["litellm"] = MagicMock()

from orion.agents.meta_search_agent import MetaSearchAgent


@pytest.mark.asyncio
async def test_meta_search_rag_integration():
    # Mock VectorStore
    mock_vector_store = AsyncMock()
    mock_doc = MagicMock()
    mock_doc.content = "Historical analysis shows momentum strategies fail in sideways markets."
    mock_vector_store.search.return_value = [mock_doc]

    # Mock MetaAgent
    mock_meta_agent = AsyncMock()
    mock_meta_agent.propose_edits.return_value = []  # Return empty to trigger fallback (or just return empty to stop)

    # Patch modules
    with (
        patch("orion.rag.vector_store.VectorStore", return_value=mock_vector_store),
        patch("orion.agents.meta_agent.MetaAgent", return_value=mock_meta_agent),
        patch("orion.agents.meta_search_agent.async_session_factory") as mock_session_factory,
    ):
        # Setup Mock DB Session
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        # Unified Mock (Solver + Metrics)
        mock_combined = MagicMock()
        # Solver Attributes
        mock_combined.solver_id = "test_solver"
        mock_combined.family_name = "momentum_v1"
        mock_combined.config = {
            "version_id": "v1",
            "base_strategy_name": "momentum_v1",
            "entry_logic": {"rules": [], "combination_method": "AND"},
            "exit_logic": {"take_profit_atr_multiple": 2.0},
        }
        # Metrics Attributes
        mock_combined.sharpe_ratio = 1.2
        mock_combined.profit_factor = 1.5
        mock_combined.info_ratio = 0.8
        mock_combined.stability_score = 0.9
        mock_combined.max_dd_pct = 10.0

        # Result Chain
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_combined
        mock_session.execute.return_value = mock_result
        mock_session.execute.side_effect = None  # Clear side effect if any

        agent = MetaSearchAgent()

        # Manually inject mock if needed, but patch should handle it if instantiated in __init__
        # Because we patched VectorStore class, agent.vector_store will be mock_vector_store

        # Run Evolution Cycle
        await agent.run_evolution_cycle("test_solver")

        # Verify RAG Search Called
        agent.vector_store.search.assert_called_once()
        args, _ = agent.vector_store.search.call_args
        assert "performance notes" in args[0]
        assert "momentum_v1" in args[0]

        # Verify Context Injection to MetaAgent
        mock_meta_agent.propose_edits.assert_called_once()
        call_args = mock_meta_agent.propose_edits.call_args
        # args[0] is config, args[1] is perf_ctx
        perf_ctx = call_args[0][1]  # second positional arg

        assert "Sharpe: 1.2" in perf_ctx
        assert "Relevant Insights" in perf_ctx
        assert "momentum strategies fail" in perf_ctx
