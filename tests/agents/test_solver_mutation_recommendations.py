"""RC.1: solver mutations are recommendations-only.

The EOD-triggered mutation path must NEVER change a solver stage, set
``is_active``, or create a paper solver. The only outcome of a qualifying
backtest is a PENDING ``PromotionRecommendation`` (research -> paper) carrying
the metrics as evidence; a non-qualifying backtest produces nothing.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from orion.agents import solver_mutation_processor as smp
from orion.storage import db
from orion.storage.models_solvers import PromotionRecommendation, Solver


def _proposal() -> dict[str, Any]:
    return {
        "type": "solver_edit",
        "mutation": {
            "base_solver_id": None,  # baseline default config
            "ops": [
                {
                    "op": "modify_param",
                    "param_name": "exit_logic.take_profit_atr_multiple",
                    "new_value": 2.5,
                    "reasoning": "test",
                }
            ],
        },
    }


def _real_eod_proposal() -> dict[str, Any]:
    """The ACTUAL EOD proposal shape: type/target_solver_id/ops at TOP level.

    Mirrors the LLM prompt contract in eod_review_agent._generate_analysis and
    what _persist_solver_edits reads (``p['target_solver_id']`` / ``p['ops']``).
    There is NO ``mutation`` block. The pre-fix processor read
    ``proposal['mutation']['ops']`` and dropped every one of these silently.
    """
    return {
        "type": "solver_edit",
        "priority": 1,
        "rationale": "ML shows tighter take-profit improves win_rate",
        "evidence_pointers": {"ml_insight": "AUC=0.85"},
        "test_plan": ["Backtest 30d"],
        "target_solver_id": None,  # baseline default config (FK-free)
        "ops": [
            {
                "op": "modify_param",
                "param_name": "exit_logic.take_profit_atr_multiple",
                "new_value": 2.5,
                "reasoning": "test",
            }
        ],
    }


def _metrics(score_source: float) -> Any:
    """A lightweight metrics stand-in with the attributes the snapshot reads."""

    class _M:
        sharpe_ratio = score_source
        profit_factor = 1.0
        info_ratio = 0.0
        stability_score = 0.0
        max_dd_pct = 0.0
        num_trades = 30

    return _M()


async def _count_rows(model: Any) -> int:
    async with db.async_session_factory() as session:
        result = await session.execute(select(model))
        return len(list(result.scalars().all()))


async def test_qualifying_mutation_persists_recommendation_no_stage_change():
    """A variant clearing the threshold yields a PENDING research->paper
    recommendation, the variant solver stays in research, and is_active=False."""
    # Score well above REFINEMENT_SCORE_THRESHOLD via a high sharpe.
    high = _metrics(10.0)

    with (
        patch.object(
            smp.MetaSearchAgent,
            "evaluate_variant",
            new=AsyncMock(return_value=(None, high)),
        ),
        patch.object(smp.MetaSearchAgent, "_calculate_composite_score", return_value=5.0),
    ):
        await smp.process_solver_mutations([_proposal()])

    recs = await _count_rows(PromotionRecommendation)
    assert recs == 1

    async with db.async_session_factory() as session:
        rec = (await session.execute(select(PromotionRecommendation))).scalars().first()
        assert rec.current_stage == "research"
        assert rec.recommended_stage == "paper"
        assert rec.status == "PENDING"
        assert rec.metrics_snapshot["composite_score"] == 5.0
        assert rec.metrics_snapshot["source"] == "eod_solver_mutation"

        solver = (await session.execute(select(Solver))).scalars().first()
        assert solver is not None
        assert solver.stage == "research"
        assert solver.is_active is False


async def test_non_qualifying_mutation_persists_no_recommendation():
    """Below the threshold: variant is still persisted in research, but NO
    recommendation row is written and no stage is changed."""
    low = _metrics(0.0)

    with (
        patch.object(
            smp.MetaSearchAgent,
            "evaluate_variant",
            new=AsyncMock(return_value=(None, low)),
        ),
        patch.object(smp.MetaSearchAgent, "_calculate_composite_score", return_value=0.0),
    ):
        await smp.process_solver_mutations([_proposal()])

    assert await _count_rows(PromotionRecommendation) == 0
    async with db.async_session_factory() as session:
        solver = (await session.execute(select(Solver))).scalars().first()
        assert solver.stage == "research"
        assert solver.is_active is False


async def test_real_eod_shape_creates_recommendation_and_counts():
    """REGRESSION (Finding 1): the real top-level solver_edit shape (no
    ``mutation`` block) must flow end-to-end and create a PromotionRecommendation.

    The pre-fix processor read ``proposal['mutation']['ops']`` → always empty →
    every real proposal returned 'skipped' → ZERO rows while reporting success.
    """
    high = _metrics(10.0)

    with (
        patch.object(
            smp.MetaSearchAgent,
            "evaluate_variant",
            new=AsyncMock(return_value=(None, high)),
        ),
        patch.object(smp.MetaSearchAgent, "_calculate_composite_score", return_value=5.0),
    ):
        counts = await smp.process_solver_mutations([_real_eod_proposal()])

    # The recommendation row must exist (the whole point of Finding 1).
    assert await _count_rows(PromotionRecommendation) == 1
    # Counts must be surfaced, and this proposal must NOT have been skipped.
    assert counts == {"processed": 1, "recommended": 1, "skipped": 0, "failed": 0}

    async with db.async_session_factory() as session:
        rec = (await session.execute(select(PromotionRecommendation))).scalars().first()
        assert rec.status == "PENDING"
        assert rec.recommended_stage == "paper"
        assert rec.metrics_snapshot["source"] == smp.EOD_MUTATION_SOURCE


async def test_empty_ops_proposal_is_skipped_in_counts():
    """A proposal with no usable ops is counted as ``skipped`` (not processed)."""
    counts = await smp.process_solver_mutations([{"type": "solver_edit", "target_solver_id": None, "ops": []}])
    assert counts == {"processed": 0, "recommended": 0, "skipped": 1, "failed": 0}
    assert await _count_rows(PromotionRecommendation) == 0


async def test_recommendation_is_idempotent_across_reruns():
    """Re-processing the same proposal does not create a duplicate PENDING
    recommendation (same dedupe discipline as the gatekeeper)."""
    high = _metrics(10.0)

    with (
        patch.object(
            smp.MetaSearchAgent,
            "evaluate_variant",
            new=AsyncMock(return_value=(None, high)),
        ),
        patch.object(smp.MetaSearchAgent, "_calculate_composite_score", return_value=5.0),
    ):
        await smp.process_solver_mutations([_proposal()])
        await smp.process_solver_mutations([_proposal()])

    assert await _count_rows(PromotionRecommendation) == 1
