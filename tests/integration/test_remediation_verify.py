from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.core.errors import ErrorCode, ModelInferenceError
from orion.core.solver_executor import SolverPipeline
from orion.core.solver_schema import SolverConfig, SolverModel
from orion.execution.execution_engine import ExecutionEngine
from orion.processing.signal_engine import SignalEngine
from orion.storage.models_gold import CandidateTrade


@pytest.mark.asyncio
async def test_fail_fast_solver_pipeline():
    """
    Verifies that SolverPipeline raises ModelInferenceError when model fails.
    """
    # Setup
    pipeline = SolverPipeline()

    # Mock Solver with a model that will fail
    solver = SolverConfig(
        version_id="FAIL_TEST",
        # base_strategy_name="TEST", # Removed
        # universe={"ticker_allowlist": ["AAPL"]}, # Universe is an object SolverUniverse
        # But schema says Optional[SolverUniverse]. If we pass dict, pydantic might parse it if it fits.
        # But let's assume strict object if possible or rely on dict parsing.
        # Pydantic 2 parsers dicts.
        universe={"ticker_allowlist": ["AAPL"]},
        model=SolverModel(model_uri="mock_fail_uri", model_version="v1"),
        risk={},
        entry_logic={"rules": [], "combination_method": "AND"},  # Dict is correct for entry_logic now
        exit_logic={},
    )

    candidate = CandidateTrade(
        candidate_id="c1", ticker="AAPL", direction="LONG", timestamp_utc="2024-01-01T12:00:00Z", confidence=0.9
    )

    # Mock get_model_registry to return a broken model via get_active_model
    mock_model = MagicMock()
    mock_model.predict_proba.side_effect = Exception("Inference Crashed")

    mock_registry_instance = MagicMock()
    mock_registry_instance.get_active_model.return_value = mock_model

    with (
        patch("orion.ml.model_registry.get_model_registry", return_value=mock_registry_instance),
        patch("orion.processing.feature_engine.FeatureEngine") as MockFeat,  # noqa: N806
    ):
        # Mock feature engine to work
        feat_instance = MockFeat.return_value
        feat_instance.compute = AsyncMock(return_value={"close": 100})

        # Execute and Expect Error
        with pytest.raises(ModelInferenceError) as excinfo:
            await pipeline.execute(solver, candidate, feature_engine=feat_instance)

        assert "Inference Failed" in str(excinfo.value)
        assert excinfo.value.code == ErrorCode.MODEL_INFERENCE_FAILED


@pytest.mark.asyncio
async def test_fail_fast_signal_engine_integration():
    """
    Verifies that SignalEngine catches the error and logs it without crashing.
    """
    engine = SignalEngine()

    # Mock Router to return our broken solver
    mock_solver = MagicMock(spec=SolverConfig)
    mock_solver.version_id = "BROKEN_SOLVER"
    mock_solver.universe = None

    # Mock Pipeline to raise ModelInferenceError
    engine.pipeline.execute = AsyncMock(side_effect=ModelInferenceError("Boom", code=ErrorCode.MODEL_INFERENCE_FAILED))

    # Mock Router
    # Mock Router
    from orion.core.solver_router import SelectedSolver

    # Needs to match SolverRouter return type: List[SelectedSolver]
    # We need to wrap our mock_solver (config) into SelectedSolver
    ss = SelectedSolver(
        solver_id="s1",
        config=mock_solver,
        info_ratio=1.0,
        oos_expect_bp=10.0,
        is_ticker_specific=False,
        is_baseline=False,
    )
    engine.router.select_solvers = AsyncMock(return_value=[ss])
    engine.regime_detector.get_current_regime_for_ticker = AsyncMock(return_value=None)

    candidate = CandidateTrade(
        candidate_id="c2", ticker="GOOG", direction="LONG", timestamp_utc="2024-01-01T12:00:00Z", confidence=0.8
    )

    # Run Decide
    decision = await engine.decide(candidate)

    # Should result in SKIP because the only solver failed
    assert decision.decision == "SKIP"
    assert "No compatible solvers" in decision.reason or "Ensemble Rejected" in decision.reason

    # Important: It did NOT crash


@pytest.mark.asyncio
async def test_execution_engine_non_blocking_init():
    """
    Verifies ExecutionEngine.__init__ does not perform async operations.
    Async initialization (risk sync from MCP) happens in initialize().
    """
    # Instantiate (engine initializes in no-op mode, connectors are None)
    executor = ExecutionEngine()

    # Verify no async operations happened during __init__
    assert executor.connector is None
    assert executor.market_connector is None
    assert executor._mcp_available is False

    # Now call initialize with MCP mocked
    executor._mcp_available = True
    executor._mcp_check_ts = None  # Force re-check

    mock_client = AsyncMock()
    mock_client.get_market_clock.return_value = {"is_open": True}
    mock_client.get_account.return_value = {"equity": "50000.0", "last_equity": "50000.0"}
    mock_client.get_positions.return_value = []
    executor._mcp_client = mock_client
    executor._get_mcp_client = lambda: mock_client

    with patch("orion.execution.execution_engine.async_session_factory"):
        executor.risk_manager.initialize = AsyncMock()
        executor.risk_manager.evaluate_drawdown_kill_switch = AsyncMock()
        await executor.initialize()

    # Verify initialize ran (risk manager was initialized)
    executor.risk_manager.initialize.assert_called_once()
