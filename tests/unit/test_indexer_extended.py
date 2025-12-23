from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orion.rag.indexer import IndexerService
from orion.storage.models_solvers import MetaExperiment, Solver


@pytest.mark.asyncio
async def test_index_meta_experiments():
    # Mock dependencies
    with patch("orion.rag.indexer.async_session_factory") as mock_session_factory:
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        # Mock VectorStore
        mock_vector_store = AsyncMock()

        # Instantiate service with mocked store
        service = IndexerService()
        service.store = mock_vector_store

        # Mock DB Result
        exp = MetaExperiment(
            experiment_id="exp_123",
            description="Test Evolution",
            status="completed",
            start_time_utc=datetime.now(timezone.utc),
            best_solver_id="sol_999",
        )

        # Mock execute result
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [exp]
        mock_session.execute.return_value = mock_result

        # Run
        await service.index_meta_experiments()

        # Verify
        mock_vector_store.add_document.assert_called_once()
        kwargs = mock_vector_store.add_document.call_args.kwargs
        assert kwargs["doc_id"] == "meta_exp_exp_123"  # doc_id
        assert "Test Evolution" in kwargs["content"]  # content
        assert kwargs["source_type"] == "META_EXPERIMENT"  # type


@pytest.mark.asyncio
async def test_index_solver_profiles():
    # Mock dependencies
    with patch("orion.rag.indexer.async_session_factory") as mock_session_factory:
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        # Mock VectorStore
        mock_vector_store = AsyncMock()

        service = IndexerService()
        service.store = mock_vector_store

        # Mock Solver
        solver = Solver(
            solver_id="sol_abc",
            family_name="TrendFollower",
            stage="live",
            is_active=True,
            config={"risk": {"max_pos": 5}},
            sharpe_ratio=1.5,
            win_rate=0.6,
            trades_count=100,
        )

        # Mock execute result
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [solver]
        mock_session.execute.return_value = mock_result

        # Run
        await service.index_solver_profiles()

        # Verify
        mock_vector_store.add_document.assert_called_once()
        kwargs = mock_vector_store.add_document.call_args.kwargs
        assert kwargs["doc_id"] == "solver_sol_abc"
        assert "TrendFollower" in kwargs["content"]
        assert "Sharpe 1.50" in kwargs["content"]
