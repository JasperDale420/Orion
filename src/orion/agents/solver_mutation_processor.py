"""Solver mutation processing for EOD agent proposals.

Takes mutation proposals from EODReviewAgent, builds the variant solver config,
backtests it, and — when the variant clears the promotion qualification bar —
persists a ``PromotionRecommendation`` row for human/policy review.

RC.1 (Wave C): this path is **recommendations-only**. It NEVER changes a solver
stage, sets ``is_active``, or creates a paper solver. The variant is always
persisted in the ``research`` stage; the only outcome of qualification is a
PENDING ``PromotionRecommendation`` (research -> paper) carrying the backtest
metrics as evidence. Symmetric with the demotion gating ("measurement before
evolution") — actual stage changes happen through the existing manual
recommendation-approval workflow (`/promotions/{id}/approve`).

The qualification bar is identical to the legacy auto-promote path: the variant
is backtested once via ``MetaSearchAgent.evaluate_variant`` and scored with
``MetaSearchAgent._calculate_composite_score``; a score at or above
``REFINEMENT_SCORE_THRESHOLD`` qualifies. The difference is purely the outcome:
a recommendation row instead of an applied promotion.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from orion.agents.meta_search_agent import (
    REFINEMENT_SCORE_THRESHOLD,
    MetaSearchAgent,
)
from orion.core.id_utils import deterministic_solver_id
from orion.core.solver_schema import EditOp, EditOpType, SolverConfig, SolverEdit
from orion.shared.db_utils import db_query, db_write
from orion.shared.logger import setup_struct_logger
from orion.storage.models_solvers import PromotionRecommendation, Solver

logger = setup_struct_logger("orion.agents.solver_mutation_processor")

# Shared marker stamped into every PromotionRecommendation produced by this
# (EOD-triggered, recommendations-only) path. The Friday auto-promoter
# (orion.jobs.solver_promoter) keys off this to leave these recommendations
# PENDING for manual approval instead of auto-approving them.
EOD_MUTATION_SOURCE = "eod_solver_mutation"


def _normalize_proposal(proposal: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
    """Collapse the two proposal shapes into one ``(base_solver_id, ops)`` DTO.

    The real EOD proposal (LLM prompt contract + ``_persist_solver_edits``)
    carries the edit target and ops at the TOP level:
    ``{"type": "solver_edit", "target_solver_id": ..., "ops": [...]}``.
    A legacy/test shape nests them under ``"mutation"``:
    ``{"mutation": {"base_solver_id": ..., "ops": [...]}}``.

    Top-level fields win when present; the nested ``mutation`` block is the
    fallback. ``base_solver_id`` may be ``None`` (meaning "baseline default").
    """
    mutation = proposal.get("mutation") or {}

    base_solver_id = proposal.get("target_solver_id")
    if base_solver_id is None:
        base_solver_id = mutation.get("base_solver_id")

    ops_data = proposal.get("ops")
    if not ops_data:
        ops_data = mutation.get("ops", [])
    if not isinstance(ops_data, list):
        ops_data = []

    return base_solver_id, ops_data


async def process_solver_mutations(proposals: list[dict[str, Any]]) -> dict[str, int]:
    """Process solver_mutation proposals as recommendations only (RC.1).

    Flow per proposal:
    1. Build the variant SolverConfig from the proposal ops.
    2. Persist the variant as a ``research``-stage Solver (no promotion).
    3. Backtest it and compute the composite qualification score.
    4. If the score clears ``REFINEMENT_SCORE_THRESHOLD``, write a PENDING
       ``PromotionRecommendation`` (research -> paper) with the metrics as
       evidence. NO solver stage is changed by this path.

    Returns a counts dict ``{processed, recommended, skipped, failed}`` so the
    caller can surface (and alarm on) the outcome:
    - ``processed`` — proposals that ran a backtest (recommended + not-qualified)
    - ``recommended`` — variants that cleared the bar and got a PENDING rec
    - ``skipped`` — proposals dropped before backtest (no usable ops)
    - ``failed`` — proposals that raised
    """
    logger.info(
        f"Processing {len(proposals)} solver mutation proposals (recommendations-only)",
        extra={"event": "solver_mutations_start", "count": len(proposals)},
    )

    meta_search = MetaSearchAgent()
    recommended_count = 0
    not_qualified_count = 0
    skipped_count = 0
    failed_count = 0

    for proposal in proposals:
        try:
            outcome = await _process_one_mutation(meta_search, proposal)
            if outcome == "recommended":
                recommended_count += 1
            elif outcome == "not_qualified":
                not_qualified_count += 1
            else:
                skipped_count += 1
        except Exception as e:
            logger.error(f"Failed to process mutation: {e}", exc_info=True)
            failed_count += 1

    processed_count = recommended_count + not_qualified_count
    counts = {
        "processed": processed_count,
        "recommended": recommended_count,
        "skipped": skipped_count,
        "failed": failed_count,
    }

    # Every proposal skipped (none reached a backtest) while proposals were
    # supplied is the silent-drop failure mode this guard exists to surface.
    if proposals and processed_count == 0 and failed_count == 0:
        logger.error(
            "Solver mutation processing dropped EVERY proposal before backtest "
            f"({skipped_count} skipped of {len(proposals)}) — likely a proposal-shape mismatch",
            extra={"event": "solver_mutations_all_skipped", **counts},
        )
    else:
        logger.info(
            "Solver mutation processing complete: "
            f"{recommended_count} recommended, {not_qualified_count} not qualified, "
            f"{skipped_count} skipped, {failed_count} failed",
            extra={"event": "solver_mutations_complete", **counts},
        )

    return counts


async def _process_one_mutation(meta_search: MetaSearchAgent, proposal: dict[str, Any]) -> str:
    """Handle a single mutation proposal.

    Returns one of ``"recommended"``, ``"not_qualified"``, ``"skipped"``.
    Raises on hard failures (caught by the caller and counted as failed).
    """
    base_solver_id, ops_data = _normalize_proposal(proposal)

    if not ops_data:
        logger.warning("Skipping mutation with no ops")
        return "skipped"

    base_config = await _load_base_config(base_solver_id)

    ops = _build_edit_ops(ops_data)
    if not ops:
        return "skipped"

    new_solver_id = deterministic_solver_id(
        base_solver_id=base_solver_id or "baseline",
        edit_ops={"ops": [o.model_dump(mode="json") for o in ops]},
        prefix="eod",
    )

    solver_edit = SolverEdit(
        base_solver_id=base_solver_id or "baseline",
        new_solver_id=new_solver_id,
        generated_by="eod_agent",
        ops=ops,
    )
    new_config = meta_search.apply_edit(base_config, solver_edit)

    # Persist the variant in research stage (idempotent). Never promoted here.
    await _persist_research_variant(new_solver_id, new_config, base_solver_id)

    logger.info(
        f"Backtesting variant {new_solver_id} for qualification",
        extra={
            "event": "mutation_qualification_start",
            "solver_id": new_solver_id,
            "base_solver_id": base_solver_id,
            "ops_count": len(ops),
        },
    )

    # Qualification logic (extracted from refine_and_promote): backtest once and
    # score. The only difference from the legacy path is the OUTCOME — a
    # recommendation, never an applied stage change.
    _solver_run, metrics = await meta_search.evaluate_variant(new_solver_id, new_config)
    score = meta_search._calculate_composite_score(metrics)

    if score >= REFINEMENT_SCORE_THRESHOLD:
        await _persist_promotion_recommendation(new_solver_id, score, metrics)
        logger.info(
            f"Variant {new_solver_id} qualified (score {score:.3f}); promotion recommendation persisted",
            extra={
                "event": "mutation_promotion_recommended",
                "solver_id": new_solver_id,
                "score": score,
            },
        )
        return "recommended"

    logger.info(
        f"Variant {new_solver_id} did not qualify (score {score:.3f} < {REFINEMENT_SCORE_THRESHOLD})",
        extra={
            "event": "mutation_not_qualified",
            "solver_id": new_solver_id,
            "score": score,
        },
    )
    return "not_qualified"


async def _load_base_config(base_solver_id: str | None) -> SolverConfig:
    """Load the base solver config, or a default baseline when none is given."""
    if base_solver_id:

        async def fetch_base(session: Any, sid: str = base_solver_id) -> Any:
            stmt = select(Solver).where(Solver.solver_id == sid)
            result = await session.execute(stmt)
            return result.scalars().first()

        base_solver = await db_query(fetch_base)
        if base_solver:
            return SolverConfig(**base_solver.config)

    from orion.core.solver_schema import ExitLogic, SolverFeatures, SolverRiskConfig

    return SolverConfig(
        version_id="baseline",
        rules=[],
        features=SolverFeatures(),
        risk=SolverRiskConfig(),
        exit_logic=ExitLogic(
            take_profit_atr_multiple=2.0,
            stop_loss_atr_multiple=1.0,
        ),
    )


def _build_edit_ops(ops_data: list[dict[str, Any]]) -> list[EditOp]:
    ops: list[EditOp] = []
    for op_data in ops_data:
        try:
            op_type = EditOpType(op_data.get("op", "modify_param"))
            ops.append(
                EditOp(
                    op=op_type,
                    param_name=op_data.get("param_name"),
                    feature_name=op_data.get("feature_name"),
                    new_value=op_data.get("new_value"),
                    reasoning=op_data.get("reasoning", "EOD agent generated"),
                )
            )
        except Exception as e:
            logger.warning(f"Skipping invalid op: {e}")
            continue
    return ops


async def _persist_research_variant(solver_id: str, config: SolverConfig, base_solver_id: str | None) -> None:
    """Insert the variant as a research-stage solver if it does not exist.

    Never sets ``is_active`` or a non-research ``stage`` — this path applies
    zero promotions.
    """

    async def save(session: Any) -> None:
        existing = (await session.execute(select(Solver).where(Solver.solver_id == solver_id))).scalars().first()
        if existing is not None:
            return
        session.add(
            Solver(
                solver_id=solver_id,
                family_name="eod_derived",
                parent_solver_id=base_solver_id,
                created_by="eod_agent",
                stage="research",
                is_active=False,
                config=config.model_dump(mode="json"),
                notes="EOD mutation variant (recommendations-only path)",
            )
        )

    await db_write(save)


async def _persist_promotion_recommendation(solver_id: str, score: float, metrics: Any) -> None:
    """Write a PENDING research->paper PromotionRecommendation with evidence.

    Idempotent: skips when a PENDING research->paper recommendation already
    exists for this solver (mirrors the gatekeeper / meta-search dedupe).
    """

    snapshot = {
        "composite_score": score,
        "threshold": REFINEMENT_SCORE_THRESHOLD,
        "sharpe_ratio": metrics.sharpe_ratio,
        "profit_factor": metrics.profit_factor,
        "info_ratio": metrics.info_ratio,
        "stability_score": metrics.stability_score,
        "max_dd_pct": metrics.max_dd_pct,
        "num_trades": metrics.num_trades,
        "source": EOD_MUTATION_SOURCE,
    }

    async def save(session: Any) -> None:
        pending = (
            (
                await session.execute(
                    select(PromotionRecommendation).where(
                        PromotionRecommendation.solver_id == solver_id,
                        PromotionRecommendation.status == "PENDING",
                        PromotionRecommendation.recommended_stage == "paper",
                    )
                )
            )
            .scalars()
            .first()
        )
        if pending is not None:
            return
        session.add(
            PromotionRecommendation(
                id=str(uuid.uuid4()),
                solver_id=solver_id,
                current_stage="research",
                recommended_stage="paper",
                reason=(
                    f"EOD mutation variant cleared qualification bar "
                    f"(composite score {score:.3f} >= {REFINEMENT_SCORE_THRESHOLD}). "
                    "Recommendation only — no stage change applied."
                ),
                metrics_snapshot=snapshot,
                status="PENDING",
                created_at_utc=datetime.now(UTC),
            )
        )

    await db_write(save)
