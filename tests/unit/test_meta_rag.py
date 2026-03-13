import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock dependencies that might be missing in test env
sys.modules["litellm"] = MagicMock()

from orion.agents.meta_search_agent import MetaSearchAgent


@pytest.mark.asyncio
async def test_meta_search_rag_integration():
    """Verify RAG vector-store search and context injection into MetaAgent.

    This test exercises _build_performance_context which queries the vector
    store and injects the results into the performance context string that
    is passed to MetaAgent.propose_edits.  We mock the vector store, the
    meta-agent, and all DB access so the test is hermetic.
    """
    # Mock VectorStore
    mock_vector_store = AsyncMock()
    mock_doc = MagicMock()
    mock_doc.content = "Historical analysis shows momentum strategies fail in sideways markets."
    mock_vector_store.search.return_value = [mock_doc]

    # Mock MetaAgent
    mock_meta_agent = AsyncMock()
    mock_meta_agent.propose_edits.return_value = []

    # Build a fake solver object that _build_performance_context reads
    mock_solver = MagicMock()
    mock_solver.solver_id = "test_solver"
    mock_solver.family_name = "momentum_v1"
    mock_solver.sharpe_ratio = 1.2
    mock_solver.config = {
        "version_id": "v1",
        "base_strategy_name": "momentum_v1",
        "entry_logic": {"rules": [], "combination_method": "AND"},
        "exit_logic": {"take_profit_atr_multiple": 2.0},
    }

    with patch("orion.rag.vector_store.VectorStore", return_value=mock_vector_store):
        agent = MetaSearchAgent()
        # Inject mock meta_agent
        agent.meta_agent = mock_meta_agent

        # Directly exercise _build_performance_context (the RAG integration
        # surface) rather than run_evolution_cycle which requires deep DB mocking.
        perf_ctx = await agent._build_performance_context(mock_solver, base_score=1.05)

        # Verify RAG Search Called
        mock_vector_store.search.assert_called_once()
        args, _ = mock_vector_store.search.call_args
        assert "performance notes" in args[0]
        assert "momentum_v1" in args[0]

        # Verify context contains both the score info and RAG insights
        assert "Sharpe: 1.2" in perf_ctx
        assert "Relevant Insights" in perf_ctx
        assert "momentum strategies fail" in perf_ctx
