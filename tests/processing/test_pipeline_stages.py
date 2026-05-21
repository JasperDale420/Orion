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
    mock_scorer.score_enriched = AsyncMock(return_value=0.8)

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
    mock_scorer.score_enriched = AsyncMock(return_value=0.3)

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


# --- Ensemble abstain semantics (FOLLOWUPS #1 — observed 2026-05-21) ---


def _mock_solver(*, solver_id: str, info_ratio: float = 1.0) -> MagicMock:
    s = MagicMock()
    s.solver_id = solver_id
    s.info_ratio = info_ratio
    s.oos_expect_bp = 50
    s.is_baseline = False
    s.config.rules = ["*"]
    s.config.exit_logic = MagicMock()
    s.config.exit_logic.fixed_sl_pct = 0.03
    s.config.exit_logic.fixed_tp_pct = 0.06
    s.config.risk = MagicMock()
    s.config.risk.risk_per_trade_bps = 150
    return s


@pytest.mark.asyncio
async def test_ensemble_treats_zero_p_take_as_abstention():
    """Three solvers; only one applies. The non-applicable solvers
    return p_take=0.0 by default (solver_executor.py:65 — initial
    value if predict_proba is not invoked). Pre-fix, the weighted
    formula counted those zeros into the denominator and EVERY
    flow-rule candidate was rejected at consensus≈0.25 even though
    the one applicable solver clearly said TAKE.

    With p_take=0.0 treated as abstention, only the active solver's
    vote contributes — consensus = 0.7 → CONTINUE.
    """
    stage = SolverEnsemble()

    s_active = _mock_solver(solver_id="bullish_sweep_paper_v1", info_ratio=1.5)
    s_abstain1 = _mock_solver(solver_id="swing_entry_paper_v1", info_ratio=1.4)
    s_abstain2 = _mock_solver(solver_id="bearish_put_paper_v1", info_ratio=1.3)

    stage.router = MagicMock()
    stage.router.select_solvers = AsyncMock(return_value=[s_active, s_abstain1, s_abstain2])

    # Return different p_take per solver via side_effect
    stage.pipeline.execute = AsyncMock(
        side_effect=[
            (0.7, 1.0, {"model": "active"}),  # active solver
            (0.0, 1.0, {"model": "inactive1"}),  # abstain
            (0.0, 1.0, {"model": "inactive2"}),  # abstain
        ]
    )

    ctx = PipelineContext(candidate=_make_candidate())
    result = await stage.evaluate(ctx)

    assert result.action == "CONTINUE", (
        "expected CONTINUE (consensus=0.7 from sole active solver), "
        "got SKIP — abstain logic broken; consensus would be 0.25"
    )
    # Consensus should be exactly the active solver's vote when others
    # abstain (1.5 * 0.7) / 1.5 = 0.7
    assert abs(ctx.consensus_score - 0.7) < 1e-9


@pytest.mark.asyncio
async def test_ensemble_skips_when_all_solvers_abstain():
    """If every solver votes p_take=0.0 (none apply), the ensemble
    should still SKIP — consensus is undefined / no positive signal."""
    stage = SolverEnsemble()

    solvers = [_mock_solver(solver_id=f"s{i}") for i in range(3)]
    stage.router = MagicMock()
    stage.router.select_solvers = AsyncMock(return_value=solvers)
    stage.pipeline.execute = AsyncMock(side_effect=[(0.0, 1.0, {})] * 3)

    ctx = PipelineContext(candidate=_make_candidate())
    result = await stage.evaluate(ctx)

    assert result.action == "SKIP"
    assert "Rejected" in result.reason or "abstain" in result.reason.lower()


@pytest.mark.asyncio
async def test_ensemble_keeps_low_but_nonzero_votes():
    """A solver that votes 0.05 (low but non-zero) is STILL voting,
    not abstaining. The fix must only exclude exact 0.0 (the default
    initialization). 0.7 + low NOs should still average below
    threshold and SKIP."""
    stage = SolverEnsemble()

    solvers = [
        _mock_solver(solver_id="s_yes", info_ratio=1.5),
        _mock_solver(solver_id="s_lownos1", info_ratio=1.4),
        _mock_solver(solver_id="s_lownos2", info_ratio=1.3),
    ]
    stage.router = MagicMock()
    stage.router.select_solvers = AsyncMock(return_value=solvers)
    stage.pipeline.execute = AsyncMock(side_effect=[(0.7, 1.0, {}), (0.05, 1.0, {}), (0.05, 1.0, {})])

    ctx = PipelineContext(candidate=_make_candidate())
    result = await stage.evaluate(ctx)

    # consensus = (0.7*1.5 + 0.05*1.4 + 0.05*1.3) / (1.5+1.4+1.3)
    # = (1.05 + 0.07 + 0.065) / 4.2 ≈ 0.282 → SKIP (still below 0.5)
    assert result.action == "SKIP", "low-but-nonzero votes must count, not abstain"
