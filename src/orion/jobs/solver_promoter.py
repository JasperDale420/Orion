"""Automated solver promotion based on pending recommendations and gate checks.

Processes PromotionRecommendation rows created by the MetaSearchAgent and
auto-approves eligible ones up to the 'paper' stage.  Promotions to
'limited_live' or 'scaled_live' are logged as warnings and left for manual
approval.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orion.shared.logger import setup_struct_logger
from orion.storage.db import async_session_factory
from orion.storage.models_solvers import PromotionRecommendation, Solver, SolverMetrics

logger = setup_struct_logger("orion.solver_promoter")

# Stages that can be auto-promoted (target stage must be in this set)
AUTO_PROMOTABLE_STAGES = {"shadow", "paper"}

# Gate thresholds
MIN_TRADES = 10
MIN_SHARPE = 0.0  # positive expectancy
MAX_DRAWDOWN_PCT = 25.0  # percent


async def _get_latest_metrics(session: AsyncSession, solver_id: str) -> SolverMetrics | None:
    """Fetch the most recent SolverMetrics row for a given solver."""
    stmt = (
        select(SolverMetrics)
        .where(SolverMetrics.solver_id == solver_id)
        .order_by(SolverMetrics.evaluated_at_utc.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalars().first()


def _check_gates(metrics: SolverMetrics) -> tuple[bool, str]:
    """Validate promotion gate criteria against solver metrics.

    Returns (passed, reason) where reason explains any failure.
    """
    if metrics.num_trades < MIN_TRADES:
        return False, f"Insufficient trades: {metrics.num_trades} < {MIN_TRADES}"

    if metrics.sharpe_ratio <= MIN_SHARPE:
        return False, f"Non-positive Sharpe ratio: {metrics.sharpe_ratio:.3f}"

    if metrics.max_dd_pct > MAX_DRAWDOWN_PCT:
        return False, f"Max drawdown too high: {metrics.max_dd_pct:.1f}% > {MAX_DRAWDOWN_PCT}%"

    return True, "All gates passed"


async def run_solver_promotions() -> dict[str, Any]:
    """Process pending promotion recommendations and auto-approve eligible ones.

    Safety: Only auto-promotes up to 'paper' stage.
    Promotions to 'limited_live' or 'scaled_live' require manual approval.

    Returns:
        Summary dict with promoted, rejected, and manual_required counts.
    """
    promoted = 0
    rejected = 0
    manual_required = 0
    details: list[dict[str, Any]] = []

    try:
        async with async_session_factory() as session:
            stmt = select(PromotionRecommendation).where(
                PromotionRecommendation.status == "PENDING",
            )
            result = await session.execute(stmt)
            pending = result.scalars().all()

            if not pending:
                logger.info("No pending promotion recommendations found")
                return {"promoted": 0, "rejected": 0, "manual_required": 0, "details": []}

            logger.info("Processing pending promotion recommendations", count=len(pending))

            for rec in pending:
                rec_detail: dict[str, Any] = {
                    "recommendation_id": rec.id,
                    "solver_id": rec.solver_id,
                    "current_stage": rec.current_stage,
                    "target_stage": rec.recommended_stage,
                }

                # Safety: require manual approval for live stages
                if rec.recommended_stage not in AUTO_PROMOTABLE_STAGES:
                    logger.warning(
                        "Promotion to live stage requires manual approval",
                        solver_id=rec.solver_id,
                        target_stage=rec.recommended_stage,
                        recommendation_id=rec.id,
                    )
                    manual_required += 1
                    rec_detail["action"] = "manual_required"
                    rec_detail["reason"] = f"Target stage '{rec.recommended_stage}' requires manual approval"
                    details.append(rec_detail)
                    continue

                # Load latest metrics
                metrics = await _get_latest_metrics(session, rec.solver_id)
                if metrics is None:
                    logger.warning(
                        "No metrics found for solver, rejecting promotion",
                        solver_id=rec.solver_id,
                        recommendation_id=rec.id,
                    )
                    rec.status = "REJECTED"
                    rec.reviewed_at_utc = datetime.now(UTC)
                    rec.reviewed_by = "auto_promoter"
                    rejected += 1
                    rec_detail["action"] = "rejected"
                    rec_detail["reason"] = "No metrics available"
                    details.append(rec_detail)
                    continue

                # Check gate criteria
                passed, reason = _check_gates(metrics)

                if not passed:
                    logger.info(
                        "Promotion gates failed",
                        solver_id=rec.solver_id,
                        target_stage=rec.recommended_stage,
                        reason=reason,
                    )
                    rec.status = "REJECTED"
                    rec.reviewed_at_utc = datetime.now(UTC)
                    rec.reviewed_by = "auto_promoter"
                    rejected += 1
                    rec_detail["action"] = "rejected"
                    rec_detail["reason"] = reason
                    details.append(rec_detail)
                    continue

                # All gates passed — promote the solver
                solver_stmt = select(Solver).where(Solver.solver_id == rec.solver_id)
                solver_result = await session.execute(solver_stmt)
                solver = solver_result.scalars().first()

                if solver is None:
                    logger.error(
                        "Solver not found for approved promotion",
                        solver_id=rec.solver_id,
                        recommendation_id=rec.id,
                    )
                    rec.status = "REJECTED"
                    rec.reviewed_at_utc = datetime.now(UTC)
                    rec.reviewed_by = "auto_promoter"
                    rejected += 1
                    rec_detail["action"] = "rejected"
                    rec_detail["reason"] = "Solver row not found"
                    details.append(rec_detail)
                    continue

                old_stage = solver.stage
                solver.stage = rec.recommended_stage
                rec.status = "APPROVED"
                rec.reviewed_at_utc = datetime.now(UTC)
                rec.reviewed_by = "auto_promoter"

                logger.info(
                    "Solver promoted",
                    solver_id=rec.solver_id,
                    old_stage=old_stage,
                    new_stage=rec.recommended_stage,
                    sharpe=metrics.sharpe_ratio,
                    trades=metrics.num_trades,
                    max_dd_pct=metrics.max_dd_pct,
                )
                promoted += 1
                rec_detail["action"] = "promoted"
                rec_detail["reason"] = reason
                details.append(rec_detail)

            await session.commit()

    except Exception:
        logger.error("Solver promotion job failed", exc_info=True)
        raise

    summary = {
        "promoted": promoted,
        "rejected": rejected,
        "manual_required": manual_required,
        "details": details,
    }
    logger.info(
        "Solver promotion run complete",
        promoted=promoted,
        rejected=rejected,
        manual_required=manual_required,
    )
    return summary


if __name__ == "__main__":
    result = asyncio.run(run_solver_promotions())
    print(f"Promoted: {result['promoted']}, Rejected: {result['rejected']}, Manual: {result['manual_required']}")
