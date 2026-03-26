from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from orion.analysis.regime import (
    MarketRegimeSnapshot,
    RiskRegime,
    SessionRegime,
    TrendRegime,
    VIXRegime,
    VolRegime,
)
from orion.core.solver_executor import SolverPipeline
from orion.processing.feature_engine import FeatureEngine
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
async def test_solver_pipeline_uses_injected_engine():
    """Verify that SolverPipeline.execute uses the passed feature_engine instance."""
    pipeline = SolverPipeline()

    mock_engine = MagicMock(spec=FeatureEngine)
    mock_engine.compute = AsyncMock(return_value={"rsi_14": 50.0})

    solver = MagicMock()
    solver.version_id = "test_solver"
    solver.rules = ["Rule_A"]
    solver.model = None
    solver.universe = None
    solver.volatility_penalty_threshold = 0.02
    solver.entry_logic = None

    candidate = CandidateTrade(
        candidate_id="c1",
        rule_id="Rule_A",
        ticker="AAPL",
        direction="LONG",
        timestamp_utc=datetime.now(UTC),
        confidence=0.8,
        evidence={},
    )

    await pipeline.execute(solver, candidate, feature_engine=mock_engine)

    mock_engine.compute.assert_called_once()
    assert mock_engine.compute.call_args[0][0] == candidate


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
async def test_signal_engine_persistence():
    """Verify SignalEngine maintains the same FeatureEngine instance across calls."""
    engine = SignalEngine()

    assert hasattr(engine, "feature_engine")
    assert isinstance(engine.feature_engine, FeatureEngine)

    original_fe = engine.feature_engine

    async def mock_regime_gate(ctx):
        ctx.regime_snapshot = _MOCK_SNAPSHOT
        ctx.regime_size_multiplier = 1.0
        return StageResult(action="CONTINUE", trace={})

    async def mock_ml_prefilter(ctx):
        ctx.ml_score = 0.7
        return StageResult(action="CONTINUE", trace={})

    async def mock_solver_ensemble(ctx):
        ctx.primary_solver_id = "test"
        ctx.consensus_score = 0.8
        ctx.limit_price = 1.0
        ctx.risk_per_trade_bps = 100
        return StageResult(action="CONTINUE", trace={"primary_solver": "test"})

    engine._stages = [
        _MockStage("regime_gate", mock_regime_gate),
        _MockStage("ml_prefilter", mock_ml_prefilter),
        _MockStage("solver_ensemble", mock_solver_ensemble),
    ]

    candidate = CandidateTrade(
        candidate_id="c1",
        rule_id="strat",
        ticker="AAPL",
        direction="LONG",
        timestamp_utc=datetime.now(UTC),
        confidence=0.8,
        evidence={},
    )

    await engine.decide(candidate)

    assert engine.feature_engine is original_fe
