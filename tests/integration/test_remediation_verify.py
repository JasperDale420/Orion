from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.analysis.regime import (
    MarketRegimeSnapshot,
    RiskRegime,
    SessionRegime,
    TrendRegime,
    VIXRegime,
    VolRegime,
)
from orion.core.errors import ErrorCode, ModelInferenceError
from orion.core.solver_executor import SolverPipeline
from orion.core.solver_schema import SolverConfig, SolverModel
from orion.execution.execution_engine import ExecutionEngine
from orion.processing.pipeline import StageResult
from orion.processing.signal_engine import SignalEngine
from orion.storage.models_gold import CandidateTrade

_MOCK_SNAPSHOT = MarketRegimeSnapshot(
    ts=datetime.now(UTC),
    trend=TrendRegime.FLAT,
    vol=VolRegime.NORMAL,
    risk=RiskRegime.NEUTRAL,
    session=SessionRegime.MIDDAY,
    vix_regime=VIXRegime.NORMAL,
)


@pytest.mark.asyncio
async def test_fail_fast_solver_pipeline():
    """Verifies that SolverPipeline raises ModelInferenceError when model fails."""
    pipeline = SolverPipeline()

    solver = SolverConfig(
        version_id="FAIL_TEST",
        universe={"ticker_allowlist": ["AAPL"]},
        model=SolverModel(model_uri="mock_fail_uri", model_version="v1"),
        risk={},
        entry_logic={"rules": [], "combination_method": "AND"},
        exit_logic={},
    )

    candidate = CandidateTrade(
        candidate_id="c1", ticker="AAPL", direction="LONG", timestamp_utc="2024-01-01T12:00:00Z", confidence=0.9
    )

    mock_model = MagicMock()
    mock_model.predict_proba.side_effect = Exception("Inference Crashed")

    mock_registry_instance = MagicMock()
    mock_registry_instance.get_active_model.return_value = mock_model

    with (
        patch("orion.ml.model_registry.get_model_registry", return_value=mock_registry_instance),
        patch("orion.processing.feature_engine.FeatureEngine") as MockFeat,  # noqa: N806
    ):
        feat_instance = MockFeat.return_value
        feat_instance.compute = AsyncMock(return_value={"close": 100})

        with pytest.raises(ModelInferenceError) as excinfo:
            await pipeline.execute(solver, candidate, feature_engine=feat_instance)

        assert "Inference Failed" in str(excinfo.value)
        assert excinfo.value.code == ErrorCode.MODEL_INFERENCE_FAILED


class _MockStage:
    def __init__(self, stage_name: str, fn):
        self._name = stage_name
        self._fn = fn

    @property
    def name(self) -> str:
        return self._name

    async def evaluate(self, ctx):
        return await self._fn(ctx)


@pytest.mark.asyncio
async def test_fail_fast_signal_engine_integration():
    """Verifies that SignalEngine catches the error and logs it without crashing."""
    engine = SignalEngine()

    async def mock_regime_gate(ctx):
        ctx.regime_snapshot = _MOCK_SNAPSHOT
        ctx.regime_size_multiplier = 1.0
        return StageResult(action="CONTINUE", trace={})

    async def mock_ml_prefilter(ctx):
        ctx.ml_score = 0.7
        return StageResult(action="CONTINUE", trace={})

    async def mock_solver_ensemble(ctx):
        raise ModelInferenceError("Boom", code=ErrorCode.MODEL_INFERENCE_FAILED)

    engine._stages = [
        _MockStage("regime_gate", mock_regime_gate),
        _MockStage("ml_prefilter", mock_ml_prefilter),
        _MockStage("solver_ensemble", mock_solver_ensemble),
    ]

    candidate = CandidateTrade(
        candidate_id="c2", ticker="GOOG", direction="LONG", timestamp_utc="2024-01-01T12:00:00Z", confidence=0.8
    )

    decision = await engine.decide(candidate)

    assert decision.decision == "SKIP"


@pytest.mark.asyncio
async def test_execution_engine_non_blocking_init():
    """Verifies ExecutionEngine.__init__ does not perform async operations."""
    executor = ExecutionEngine()

    assert executor._gateway_available is False

    executor._gateway_available = True
    executor._gateway_check_ts = None

    mock_client = AsyncMock()
    mock_client.get_clock.return_value = {"is_open": True}
    mock_client.get_account.return_value = {"equity": "50000.0", "last_equity": "50000.0"}
    mock_client.get_positions.return_value = []
    executor._gateway_client = mock_client
    executor._get_gateway_client = lambda: mock_client

    with patch("orion.execution.execution_engine.async_session_factory"):
        executor.risk_manager.initialize = AsyncMock()
        executor.risk_manager.evaluate_drawdown_kill_switch = AsyncMock()
        await executor.initialize()

    executor.risk_manager.initialize.assert_called_once()
