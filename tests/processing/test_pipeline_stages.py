"""
Tests for composable pipeline stages (RegimeGate, MLPreFilter, SolverEnsemble).

Each stage is tested in isolation to verify it correctly returns CONTINUE or SKIP
and populates PipelineContext fields for downstream stages.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.analysis.regime import MarketRegimeSnapshot, RiskRegime, SessionRegime, TrendRegime, VIXRegime, VolRegime
from orion.processing.pipeline import PipelineContext, StageResult
from orion.processing.stages.ml_prefilter import MLPreFilter
from orion.processing.stages.regime_gate import RegimeGate
from orion.processing.stages.solver_ensemble import SolverEnsemble
from orion.storage.models_gold import CandidateTrade


def _make_candidate(**overrides) -> CandidateTrade:
    defaults = dict(
        candidate_id="test_cand_1",
        ticker="AAPL",
        timestamp_utc=datetime.now(UTC),
        rule_id="rule_test",
        direction="LONG",
        confidence=0.8,
        evidence={},
    )
    defaults.update(overrides)
    return CandidateTrade(**defaults)


# --- RegimeGate ---


@pytest.mark.asyncio
async def test_regime_gate_continues_in_normal_conditions():
    gate = RegimeGate()

    # Mock detector to return a benign regime
    snapshot = MarketRegimeSnapshot(
        ts=datetime.now(UTC),
        trend=TrendRegime.FLAT,
        vol=VolRegime.NORMAL,
        risk=RiskRegime.NEUTRAL,
        session=SessionRegime.MIDDAY,
        vix_regime=VIXRegime.NORMAL,
    )
    gate.multi_axis_detector.detect = MagicMock(return_value=snapshot)

    ctx = PipelineContext(candidate=_make_candidate())
    result = await gate.evaluate(ctx)

    assert result.action == "CONTINUE"
    assert ctx.regime_snapshot is snapshot
    assert ctx.regime_size_multiplier > 0


@pytest.mark.asyncio
async def test_regime_gate_skips_on_shock():
    gate = RegimeGate()

    # SHOCK should trigger SKIP
    snapshot = MarketRegimeSnapshot(
        ts=datetime.now(UTC),
        trend=TrendRegime.FLAT,
        vol=VolRegime.SHOCK,
        risk=RiskRegime.RISK_OFF,
        session=SessionRegime.MIDDAY,
        vix_regime=VIXRegime.EXTREME,
    )
    gate.multi_axis_detector.detect = MagicMock(return_value=snapshot)

    ctx = PipelineContext(candidate=_make_candidate())
    result = await gate.evaluate(ctx)

    assert result.action == "SKIP"
    assert "SHOCK" in result.reason
    assert result.trace.get("regime_blocked") is True


@pytest.mark.asyncio
async def test_regime_gate_populates_context():
    gate = RegimeGate()

    snapshot = MarketRegimeSnapshot(
        ts=datetime.now(UTC),
        trend=TrendRegime.UP,
        vol=VolRegime.LOW,
        risk=RiskRegime.RISK_ON,
        session=SessionRegime.POWER_HOUR,
        vix_regime=VIXRegime.LOW,
    )
    gate.multi_axis_detector.detect = MagicMock(return_value=snapshot)

    ctx = PipelineContext(candidate=_make_candidate())
    result = await gate.evaluate(ctx)

    assert result.action == "CONTINUE"
    assert ctx.regime_snapshot.trend == TrendRegime.UP
    assert ctx.regime_snapshot.vol == VolRegime.LOW
    # Size multiplier should reflect favorable conditions
    assert ctx.regime_size_multiplier > 0


# --- MLPreFilter ---


@pytest.mark.asyncio
async def test_ml_prefilter_continues_above_threshold():
    stage = MLPreFilter()

    mock_scorer = MagicMock()
    mock_scorer.bypass_scoring = False
    mock_scorer.score.return_value = 0.8

    ctx = PipelineContext(
        candidate=_make_candidate(
            option_type="CALL",
            premium=2.0,
            strike_price=200.0,
            evidence={"premium_usd": 100000, "put_call": "C"},
        )
    )

    with patch("orion.ml.scorer.get_scorer", return_value=mock_scorer):
        result = await stage.evaluate(ctx)

    assert result.action == "CONTINUE"
    assert ctx.ml_score == 0.8


@pytest.mark.asyncio
async def test_ml_prefilter_skips_below_threshold():
    stage = MLPreFilter()

    mock_scorer = MagicMock()
    mock_scorer.bypass_scoring = False
    mock_scorer.score.return_value = 0.3

    ctx = PipelineContext(
        candidate=_make_candidate(
            option_type="CALL",
            premium=2.0,
            evidence={"premium_usd": 100000, "put_call": "C"},
        )
    )

    with patch("orion.ml.scorer.get_scorer", return_value=mock_scorer):
        result = await stage.evaluate(ctx)

    assert result.action == "SKIP"
    assert "pre-filter" in result.reason


@pytest.mark.asyncio
async def test_ml_prefilter_bypasses_incomplete_context():
    """No premium/put_call → bypass ML check, CONTINUE."""
    stage = MLPreFilter()

    ctx = PipelineContext(candidate=_make_candidate(evidence={}))

    result = await stage.evaluate(ctx)

    assert result.action == "CONTINUE"
    assert "bypassed" in str(result.trace.get("ml_prefilter", ""))


@pytest.mark.asyncio
async def test_ml_prefilter_continues_on_scorer_error():
    """ML scorer failure → don't block, CONTINUE."""
    stage = MLPreFilter()

    ctx = PipelineContext(
        candidate=_make_candidate(
            option_type="CALL",
            premium=2.0,
            evidence={"premium_usd": 100000, "put_call": "C"},
        )
    )

    with patch("orion.ml.scorer.get_scorer", side_effect=RuntimeError("model load failed")):
        result = await stage.evaluate(ctx)

    assert result.action == "CONTINUE"
    assert "error" in str(result.trace)


# --- SolverEnsemble ---


@pytest.mark.asyncio
async def test_solver_ensemble_skips_when_router_empty():
    stage = SolverEnsemble()
    stage.router = MagicMock()
    stage.router.select_solvers = AsyncMock(return_value=[])

    ctx = PipelineContext(candidate=_make_candidate())
    result = await stage.evaluate(ctx)

    assert result.action == "SKIP"
    assert "Fallback" in result.reason or "Router empty" in result.reason
    assert result.trace.get("fallback_triggered") is True


@pytest.mark.asyncio
async def test_solver_ensemble_skips_below_consensus():
    stage = SolverEnsemble()

    solver_obj = MagicMock()
    solver_obj.solver_id = "s1"
    solver_obj.info_ratio = 1.0
    solver_obj.oos_expect_bp = 50
    solver_obj.is_baseline = False
    solver_obj.config.rules = ["*"]
    solver_obj.config.exit_logic = None
    solver_obj.config.risk = None

    stage.router = MagicMock()
    stage.router.select_solvers = AsyncMock(return_value=[solver_obj])
    stage.pipeline.execute = AsyncMock(return_value=(0.2, 1.0, {}))

    ctx = PipelineContext(candidate=_make_candidate())
    result = await stage.evaluate(ctx)

    assert result.action == "SKIP"
    assert "Rejected" in result.reason


@pytest.mark.asyncio
async def test_solver_ensemble_continues_above_consensus():
    stage = SolverEnsemble()

    solver_obj = MagicMock()
    solver_obj.solver_id = "s1"
    solver_obj.info_ratio = 2.0
    solver_obj.oos_expect_bp = 100
    solver_obj.is_baseline = False
    solver_obj.config.rules = ["*"]
    solver_obj.config.exit_logic = MagicMock()
    solver_obj.config.exit_logic.fixed_sl_pct = 0.03
    solver_obj.config.exit_logic.fixed_tp_pct = 0.06
    solver_obj.config.risk = MagicMock()
    solver_obj.config.risk.risk_per_trade_bps = 150

    stage.router = MagicMock()
    stage.router.select_solvers = AsyncMock(return_value=[solver_obj])
    stage.pipeline.execute = AsyncMock(return_value=(0.85, 1.0, {"model": "v1"}))

    ctx = PipelineContext(candidate=_make_candidate())
    result = await stage.evaluate(ctx)

    assert result.action == "CONTINUE"
    assert ctx.consensus_score == 0.85
    assert ctx.primary_solver_id == "s1"
    assert ctx.stop_loss_pct == 0.03
    assert ctx.take_profit_pct == 0.06
    assert ctx.risk_per_trade_bps == 150.0
