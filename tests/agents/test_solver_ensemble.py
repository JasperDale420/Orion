"""
Tests for ensemble decision behavior via the composable pipeline.

Verifies consensus and rejection through the pipeline stages.
"""

from datetime import UTC, datetime

import pytest

from orion.analysis.regime import (
    MarketRegimeSnapshot,
    RiskRegime,
    SessionRegime,
    TrendRegime,
    VIXRegime,
    VolRegime,
)
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


class _MockStage:
    """Simple mock stage matching PipelineStage protocol."""

    def __init__(self, stage_name: str, fn):
        self._name = stage_name
        self._fn = fn

    @property
    def name(self) -> str:
        return self._name

    async def evaluate(self, ctx):
        return await self._fn(ctx)


async def _regime_pass(ctx):
    ctx.regime_snapshot = _MOCK_SNAPSHOT
    ctx.regime_size_multiplier = 1.0
    return StageResult(action="CONTINUE", trace={"regime_gate": "passed"})


async def _ml_pass(ctx):
    ctx.ml_score = 0.8
    return StageResult(action="CONTINUE", trace={"ml_prefilter": "passed"})


@pytest.mark.asyncio
async def test_ensemble_decision_consensus():
    """Solver ensemble produces EXECUTE when consensus exceeds threshold."""
    engine = SignalEngine()

    async def solver_consensus(ctx):
        ctx.primary_solver_id = "s1"
        ctx.consensus_score = 0.75
        ctx.limit_price = 2.50
        ctx.risk_per_trade_bps = 100
        return StageResult(
            action="CONTINUE",
            trace={"primary_solver": "s1", "ensemble_consensus_score": 0.75},
        )

    engine._stages = [
        _MockStage("regime_gate", _regime_pass),
        _MockStage("ml_prefilter", _ml_pass),
        _MockStage("solver_ensemble", solver_consensus),
    ]

    candidate = CandidateTrade(
        candidate_id="c1",
        source="UW",
        ticker="AAPL",
        timestamp_utc=datetime.now(UTC),
        rule_id="rule_sweep",
        confidence=0.5,
        direction="LONG",
        evidence={"event_id": "e1"},
    )

    decision = await engine.decide(candidate)

    assert decision.decision == "EXECUTE"
    ensemble_trace = decision.decision_trace_json.get("solver_ensemble", {})
    assert ensemble_trace.get("ensemble_consensus_score") == 0.75


@pytest.mark.asyncio
async def test_ensemble_decision_rejection():
    """Solver ensemble produces SKIP when consensus is below threshold."""
    engine = SignalEngine()

    async def solver_reject(ctx):
        return StageResult(action="SKIP", reason="Ensemble Rejected: consensus 0.20 < 0.50", trace={})

    engine._stages = [
        _MockStage("regime_gate", _regime_pass),
        _MockStage("ml_prefilter", _ml_pass),
        _MockStage("solver_ensemble", solver_reject),
    ]

    candidate = CandidateTrade(
        candidate_id="c2",
        source="UW",
        ticker="TSLA",
        timestamp_utc=datetime.now(UTC),
        rule_id="rule_sweep",
        confidence=0.5,
        direction="SHORT",
        evidence={"event_id": "e2"},
    )

    decision = await engine.decide(candidate)

    assert decision.decision == "SKIP"
    assert "Ensemble Rejected" in decision.reason
