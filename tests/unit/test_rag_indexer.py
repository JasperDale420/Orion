# Import the module under test
# We need to sys.modules mock some dependencies if they fail to import due to missing env vars
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.modules["orion.rag.embeddings"] = MagicMock()

from orion.rag.indexer import IndexerService
from orion.storage.models_gold import CandidateTrade, StrategyDecision
from orion.storage.models_solvers import MetaExperiment, Solver, SolverMetrics


@pytest.mark.asyncio
async def test_indexer_service_methods():
    # Mock VectorStore
    with patch("orion.rag.indexer.VectorStore") as MockStoreClass:
        mock_store = AsyncMock()
        MockStoreClass.return_value = mock_store

        # Mock DB Session
        with (
            patch("orion.rag.indexer.async_session_factory") as mock_session_factory,
            patch("orion.rag.indexer.init_db", new_callable=AsyncMock),
        ):
            mock_session = AsyncMock()
            mock_session_factory.return_value.__aenter__.return_value = mock_session

            # Setup Mock Data for Candidates
            mock_cand = CandidateTrade(
                candidate_id="c1",
                ticker="AAPL",
                timestamp_utc=datetime.now(),
                rule_id="rule1",
                direction="LONG",
                confidence=0.9,
                evidence={"signal_id": "s1"},
            )

            # Setup Mock Data for Metrics
            mock_metric = SolverMetrics(
                id="m1",
                solver_id="solv1",
                dataset_tag="test",
                evaluated_at_utc=datetime.now(),
                sharpe_ratio=2.0,
                profit_factor=1.5,
                max_dd_pct=10.0,
                num_trades=50,
                stability_score=0.8,
            )

            # Setup Mock Data for Decisions
            mock_decision = StrategyDecision(
                decision_id="d1",
                ticker="GOOG",
                decision="EXECUTE",
                strategy_version_id="solv1",
                reason="Good trade",
                executed_successfully="TRUE",
                timestamp_utc=datetime.now(),
            )

            # Setup Mock Data for Meta Experiments
            mock_exp = MetaExperiment(
                experiment_id="e1",
                description="Test experiment",
                status="completed",
                best_solver_id="solv1",
                start_time_utc=datetime.now(),
                end_time_utc=datetime.now(),
            )

            # Setup Mock Data for Solver Profiles
            mock_solver = Solver(
                solver_id="solv1",
                family_name="TestSolver",
                config={"model": {"model_version": "m1"}},
                is_active=True,
                stage="research",
            )

            # Configure execute results
            # index_all sequence:
            # 1. candidates
            # 2. metrics
            # 3. meta experiments
            # 4. solver profiles
            # 5. decisions

            mock_result_cand = MagicMock()
            mock_result_cand.scalars.return_value.all.return_value = [mock_cand]

            mock_result_met = MagicMock()
            mock_result_met.scalars.return_value.all.return_value = [mock_metric]

            mock_result_dec = MagicMock()
            mock_result_dec.scalars.return_value.all.return_value = [mock_decision]

            mock_result_exp = MagicMock()
            mock_result_exp.scalars.return_value.all.return_value = [mock_exp]

            mock_result_solver = MagicMock()
            mock_result_solver.scalars.return_value.all.return_value = [mock_solver]

            mock_session.execute.side_effect = [
                mock_result_cand,
                mock_result_met,
                mock_result_exp,
                mock_result_solver,
                mock_result_dec,
            ]

            service = IndexerService()
            await service.index_all()

            # Verify Calls
            assert mock_store.add_document.call_count == 5

            # Verify content of calls
            calls = mock_store.add_document.call_args_list

            # Call 1: Candidate
            args1, kwargs1 = calls[0]
            assert kwargs1["source_type"] == "CANDIDATE_TRADE"
            assert "AAPL" in kwargs1["content"]

            # Call 2: Metrics
            args2, kwargs2 = calls[1]
            assert kwargs2["source_type"] == "SOLVER_METRICS"
            assert "Sharpe: 2.0" in kwargs2["content"]

            # Call 3: Meta experiment
            args3, kwargs3 = calls[2]
            assert kwargs3["source_type"] == "META_EXPERIMENT"
            assert "Test experiment" in kwargs3["content"]

            # Call 4: Solver profile
            args4, kwargs4 = calls[3]
            assert kwargs4["source_type"] == "SOLVER_PROFILE"
            assert "TestSolver" in kwargs4["content"]

            # Call 5: Decision
            args5, kwargs5 = calls[4]
            assert kwargs5["source_type"] == "STRATEGY_DECISION"
            assert "EXECUTE" in kwargs5["content"]
