import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from orion.core.promotion_rules import DEFAULT_PROMOTION_CONFIG, evaluate_stage_transition
from orion.shared.logger import setup_struct_logger
from orion.storage.db import async_session_factory
from orion.storage.models_solvers import PromotionRecommendation, Solver, SolverMetrics
from sqlalchemy import select

logger = setup_struct_logger("orion.gatekeeper")


class Gatekeeper:
    """
    Automated job to audit Solvers and promote/demote based on performance.
    Prioritizes safety (Demotion) over speed (Promotion).
    """

    async def run_once(self) -> None:
        """
        Single execution pass:
        1. Fetch all active or candidate Solvers.
        2. Fetch their latest 'test' or 'live' metrics.
        3. Evaluate transition.
        4. Update DB.
        """
        logger.info("Starting Gatekeeper Audit...")

        async with async_session_factory() as session:
            try:
                # Fetch Solvers
                # For V1, we scan ALL solvers to ensure we catch demotions needing attention
                stmt = select(Solver)
                result = await session.execute(stmt)
                solvers = result.scalars().all()

                updates_count = 0

                for solver in solvers:
                    # Skip deprecated
                    if solver.stage == "deprecated":
                        continue

                    # Fetch latest metrics
                    # Which metrics? For Promotion, usually 'test' (OOS) or 'live' (Paper/Live).
                    # Logic needs to know context.
                    # Simplified V1:
                    # - If Research -> check 'test' metrics
                    # - If Shadow/Paper/Live -> check 'live' metrics (or shadow replay)

                    # Shadow might log to 'shadow' tag? Let's assume 'test' for research and 'live' for everything else implies real-time data flow.
                    # But wait, Shadow doesn't trade. It logs 'shadow'?
                    # Let's fallback to checking latest metric of ANY appropriate tag.

                    # Fetch MOST RECENT metrics for this solver
                    # Order by evaluated_at_utc desc
                    m_stmt = (
                        select(SolverMetrics)
                        .where(SolverMetrics.solver_id == solver.solver_id)
                        .order_by(SolverMetrics.evaluated_at_utc.desc())
                        .limit(1)
                    )
                    m_res = await session.execute(m_stmt)
                    metrics = m_res.scalars().first()

                    if not metrics:
                        continue

                    action = evaluate_stage_transition(metrics, solver.stage, DEFAULT_PROMOTION_CONFIG)

                    if action == "maintain":
                        continue

                    if action == "demote":
                        await self._handle_demotion(session, solver, metrics)
                        updates_count += 1
                    elif action == "promote":
                        await self._handle_promotion(session, solver, metrics)
                        updates_count += 1

                await session.commit()
                if updates_count > 0:
                    logger.info(f"Gatekeeper Audit Complete. Updates: {updates_count}")
                else:
                    logger.info("Gatekeeper Audit Complete. No changes.")

            except Exception as e:
                logger.error(f"Gatekeeper Run Failed: {e}")
                await session.rollback()

    async def _handle_demotion(self, session: Any, solver: Solver, metrics: SolverMetrics) -> None:
        old_stage = solver.stage

        # Demotion Logic:
        # If Live/Paper -> Pause (is_active=False) and set stage to 'paused' or move down?
        # PRD 9.1: "demoted one level (or paused)"
        # Safety first: PAUSE.

        # Safety: stop trading immediately, but do not mutate stage directly.
        # PRDv2 FR 5.5.2: stage changes require an explicit promotion workflow.
        solver.is_active = False
        solver.status = "deprecated"

        stage_order = ["research", "shadow", "paper", "limited_live", "scaled_live"]
        try:
            current_idx = stage_order.index(old_stage)
            recommended_stage = stage_order[current_idx - 1] if current_idx > 0 else old_stage
        except ValueError:
            recommended_stage = old_stage

        # Avoid duplicating pending recommendations for the same solver+target.
        existing_stmt = select(PromotionRecommendation).where(
            PromotionRecommendation.solver_id == solver.solver_id,
            PromotionRecommendation.status == "PENDING",
            PromotionRecommendation.recommended_stage == recommended_stage,
        )
        existing = (await session.execute(existing_stmt)).scalars().first()
        if not existing:
            session.add(
                PromotionRecommendation(
                    id=str(uuid.uuid4()),
                    solver_id=solver.solver_id,
                    current_stage=old_stage,
                    recommended_stage=recommended_stage,
                    reason="Demotion criteria breached (automated health monitor).",
                    metrics_snapshot={
                        "dataset_tag": metrics.dataset_tag,
                        "num_trades": metrics.num_trades,
                        "profit_factor": metrics.profit_factor,
                        "max_dd_pct": metrics.max_dd_pct,
                        "evaluated_at_utc": metrics.evaluated_at_utc.isoformat() if metrics.evaluated_at_utc else None,
                        "metrics_json": metrics.metrics_json,
                    },
                    status="PENDING",
                    created_at_utc=datetime.now(timezone.utc),
                )
            )

        logger.warning(
            f"DEMOTING Solver {solver.family_name} ({solver.solver_id}). "
            f"Stage: {old_stage}. Validating Metrics: DD={metrics.max_dd_pct}%, PF={metrics.profit_factor}."
        )
        # We could notify here (e.g. valid 'incident')

    async def _handle_promotion(self, session: Any, solver: Solver, metrics: SolverMetrics) -> None:
        old_stage = solver.stage
        stage_order = ["research", "shadow", "paper", "limited_live", "scaled_live"]

        try:
            current_idx = stage_order.index(old_stage)
            if current_idx >= len(stage_order) - 1:
                return  # Max stage

            new_stage = stage_order[current_idx + 1]

            # PRDv2 FR 5.5.2: recommendation vs decision. Create a recommendation, don't mutate stage here.
            existing_stmt = select(PromotionRecommendation).where(
                PromotionRecommendation.solver_id == solver.solver_id,
                PromotionRecommendation.status == "PENDING",
                PromotionRecommendation.recommended_stage == new_stage,
            )
            existing = (await session.execute(existing_stmt)).scalars().first()
            if not existing:
                session.add(
                    PromotionRecommendation(
                        id=str(uuid.uuid4()),
                        solver_id=solver.solver_id,
                        current_stage=old_stage,
                        recommended_stage=new_stage,
                        reason="Metrics met promotion criteria (automated gatekeeper).",
                        metrics_snapshot={
                            "dataset_tag": metrics.dataset_tag,
                            "num_trades": metrics.num_trades,
                            "profit_factor": metrics.profit_factor,
                            "max_dd_pct": metrics.max_dd_pct,
                            "evaluated_at_utc": (
                                metrics.evaluated_at_utc.isoformat() if metrics.evaluated_at_utc else None
                            ),
                            "metrics_json": metrics.metrics_json,
                        },
                        status="PENDING",
                        created_at_utc=datetime.now(timezone.utc),
                    )
                )

            logger.info(
                f"PROMOTION RECOMMENDED for Solver {solver.family_name} ({solver.solver_id}) "
                f"From {old_stage} -> {new_stage}. Metric: PF={metrics.profit_factor}"
            )

        except ValueError:
            logger.error(f"Unknown stage {old_stage} for solver {solver.solver_id}")


if __name__ == "__main__":
    # Simple CLI wrapper
    asyncio.run(Gatekeeper().run_once())
