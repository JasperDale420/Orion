from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from orion.core.solver_executor import SolverPipeline
from orion.processing.feature_engine import FeatureEngine
from orion.processing.signal_engine import SignalEngine
from orion.storage.models_gold import CandidateTrade


@pytest.mark.asyncio
async def test_solver_pipeline_uses_injected_engine():
    """
    Verify that SolverPipeline.execute uses the passed feature_engine instance.
    """
    pipeline = SolverPipeline()

    # Mock Objects
    mock_engine = MagicMock(spec=FeatureEngine)
    mock_engine.compute = AsyncMock(return_value={"rsi_14": 50.0})

    # Mock Solver Config to bypass Pydantic issues in test env
    solver = MagicMock()
    solver.version_id = "test_solver"
    solver.rules = ["Rule_A"]
    solver.rules = ["Rule_A"]
    solver.rules = ["Rule_A"]
    solver.model = None
    solver.universe = None
    solver.volatility_penalty_threshold = 0.02  # Fix float comparison

    # SolverPipeline accesses solver.rules and solver.model
    # It also accesses solver.entry_logic for weighting (if implemented)
    solver.entry_logic = None

    candidate = CandidateTrade(
        candidate_id="c1",
        rule_id="Rule_A",  # Required
        ticker="AAPL",
        direction="LONG",
        timestamp_utc=datetime.now(timezone.utc),
        confidence=0.8,
        evidence={},
    )
    # Patch rule check to pass
    solver.rules = ["Rule_A"]

    # Execute
    await pipeline.execute(solver, candidate, feature_engine=mock_engine)

    # Assert
    mock_engine.compute.assert_called_once()
    assert mock_engine.compute.call_args[0][0] == candidate  # Arg 0 is candidate


@pytest.mark.asyncio
async def test_signal_engine_persistence():
    """
    Verify SignalEngine maintains the same FeatureEngine instance across calls.
    """
    # Instantiate Engine
    engine = SignalEngine()

    # Check it has a feature engine
    assert hasattr(engine, "feature_engine")
    assert isinstance(engine.feature_engine, FeatureEngine)

    original_fe = engine.feature_engine

    # Mock dependencies to avoid DB calls
    # Mock Solver Configs
    s1 = MagicMock()
    s1.version_id = "s1"
    s1.rules = []

    engine.router.select_solvers = AsyncMock(return_value=[s1])
    engine.regime_detector.get_current_regime_for_ticker = AsyncMock(return_value=None)
    engine.pipeline.execute = AsyncMock(return_value=(0.5, 1.0, {}))

    # Create Candidate
    candidate = CandidateTrade(
        candidate_id="c1",
        rule_id="strat",  # Matches logic if needed, or arbitrary
        ticker="AAPL",
        direction="LONG",
        timestamp_utc=datetime.now(timezone.utc),
        confidence=0.8,
        evidence={},
    )

    # Call Decide
    await engine.decide(candidate)

    # Verify pipeline execute called with specific kwarg
    engine.pipeline.execute.assert_called_once()

    call_kwargs = engine.pipeline.execute.call_args[1]
    assert "feature_engine" in call_kwargs
    assert call_kwargs["feature_engine"] is original_fe

    # Verify instance hasn't changed
    assert engine.feature_engine is original_fe
