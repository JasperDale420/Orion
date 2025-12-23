import logging
from dataclasses import dataclass
from typing import List

from sqlalchemy import select

from orion.core.solver_schema import SolverConfig
from orion.storage.db import async_session_factory  # Async session logic
from orion.storage.models_solvers import Solver, SolverMetrics

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SelectedSolver:
    solver_id: str
    config: SolverConfig
    info_ratio: float
    oos_expect_bp: float
    is_ticker_specific: bool
    is_baseline: bool


class SolverRouter:
    """
    Selects the best Solver for a given trading context.
    PRDv2 Addendum 5.6.1.
    """

    def __init__(self):
        pass

    async def select_solvers(self, context: "LiveContext", top_k: int = 3) -> List[SelectedSolver]:
        """
        Determines which solvers to use (Async).
        Returns a list of eligible solvers for Ensemble Logic (FR 5.6.2).

        Args:
            context: LiveContext object
            top_k: Max solvers to return.

        Returns:
            List[SelectedSolver] (solver config + metrics for weighting).
        """
        # Lazy import to avoid circular dependency
        from orion.config import system_settings

        baseline_id = system_settings.baseline_solver_id

        async with async_session_factory() as session:
            try:
                # PRDv2 §4.1/§11: Gate selection by status='active' (with legacy is_active fallback).
                stmt = select(Solver).where(
                    (Solver.status == "active") | ((Solver.status.is_(None)) & (Solver.is_active))
                )
                result = await session.execute(stmt)
                active_solvers = result.scalars().all()

                if not active_solvers:
                    return []

                ranked_candidates: list[tuple[SelectedSolver, float]] = []
                target_ticker = context.ticker

                for s in active_solvers:
                    try:
                        cfg_blob = s.definition_json or s.config
                        cfg = SolverConfig(**cfg_blob)
                    except Exception as e:
                        logger.warning(f"Skipping invalid solver config {s.solver_id}: {e}")
                        continue

                    # --- Context Filtering ---
                    target_stage = context.current_stage  # Default to paper safety handled in init or caller

                    # 1. Stage Check (Dynamic)
                    from orion.core.promotion_rules import STAGE_ORDER

                    # Create rank map: research=0, shadow=1, paper=2, limited_live=3, scaled_live=4
                    stage_rank = {s: i for i, s in enumerate(STAGE_ORDER)}

                    # Alias "live" to "limited_live" (min viable live) for context checking
                    if target_stage == "live":
                        target_stage_val = stage_rank.get("limited_live", 3)
                    else:
                        target_stage_val = stage_rank.get(target_stage, 2)

                    s_stage = s.stage
                    if s_stage == "live":
                        s_stage = "limited_live"
                    solver_stage_val = stage_rank.get(s_stage, 0)

                    if solver_stage_val < target_stage_val:
                        continue

                    # Pull latest solver_metrics for ranking/constraints (PRD FR 5.6.1).
                    stmt_m = (
                        select(SolverMetrics)
                        .where(SolverMetrics.solver_id == s.solver_id)
                        .order_by(SolverMetrics.evaluated_at_utc.desc())
                        .limit(1)
                    )
                    m_res = await session.execute(stmt_m)
                    latest_metrics = m_res.scalars().first()
                    if latest_metrics is None:
                        # PRDv2 §11.2 requires expected_return/risk_score for live signals; require metrics for paper/live routing.
                        if (
                            target_stage in ["paper", "limited_live", "scaled_live", "live"]
                            and s.solver_id != baseline_id
                        ):
                            continue
                        latest_metrics_score = -1e9
                        max_dd_pct = 0.0
                        info_ratio = 0.0
                        oos_expect_bp = 0.0
                    else:
                        latest_metrics_score = float(latest_metrics.info_ratio or 0.0)
                        max_dd_pct = float(latest_metrics.max_dd_pct or 0.0)
                        info_ratio = float(latest_metrics.info_ratio or 0.0)
                        oos_expect_bp = float(latest_metrics.oos_expect_bp or 0.0)

                    if target_stage in ["paper", "limited_live", "scaled_live", "live"] and oos_expect_bp == 0.0:
                        continue

                    # Basic drawdown constraint (PRD: subject to max drawdown constraints).
                    if max_dd_pct > 25.0:
                        continue

                    if cfg.universe:
                        # 2. Ticker Allowlist
                        if cfg.universe.ticker_allowlist:
                            if not target_ticker:
                                continue
                            if target_ticker not in cfg.universe.ticker_allowlist:
                                continue
                            is_ticker_specific = True
                        else:
                            is_ticker_specific = False

                        # 3. Ticker Blocklist
                        if cfg.universe.ticker_blocklist and target_ticker:
                            if target_ticker in cfg.universe.ticker_blocklist:
                                continue

                        # 4. Regime Check
                        required_regime = cfg.universe.required_regime
                        current_regime = context.regime

                        # Strict Regime Compliance
                        if required_regime:
                            if not current_regime or current_regime == "UNKNOWN":
                                logger.debug(
                                    f"Solver {s.solver_id} requires regime {required_regime} but current is UNKNOWN. Skipping."
                                )
                                continue

                            if required_regime != current_regime:
                                logger.debug(
                                    f"Solver {s.solver_id} rejected: regime {required_regime} != {current_regime}"
                                )
                                continue
                    else:
                        is_ticker_specific = False

                    ranked_candidates.append(
                        (
                            SelectedSolver(
                                solver_id=s.solver_id,
                                config=cfg,
                                info_ratio=info_ratio,
                                oos_expect_bp=oos_expect_bp,
                                is_ticker_specific=is_ticker_specific,
                                is_baseline=(baseline_id is not None and s.solver_id == baseline_id),
                            ),
                            latest_metrics_score,
                        )
                    )

                if not ranked_candidates:
                    # Fallback Logic (FR 5.6.3)
                    fallback_id = baseline_id

                    if fallback_id:
                        # Find the baseline solver in the active list (it should be active if it's the baseline)
                        fallback_solver = next((s for s in active_solvers if s.solver_id == fallback_id), None)

                        if not fallback_solver:
                            # Try fetching explicitly
                            stmt_fb = select(Solver).where(Solver.solver_id == fallback_id)
                            res_fb = await session.execute(stmt_fb)
                            fallback_solver = res_fb.scalars().first()

                        if fallback_solver:
                            try:
                                fb_cfg_blob = fallback_solver.definition_json or fallback_solver.config
                                fb_cfg = SolverConfig(**fb_cfg_blob)
                                # Best-effort metrics: if missing, keep deterministic safe defaults.
                                stmt_fb_m = (
                                    select(SolverMetrics)
                                    .where(SolverMetrics.solver_id == fallback_solver.solver_id)
                                    .order_by(SolverMetrics.evaluated_at_utc.desc())
                                    .limit(1)
                                )
                                fb_m = (await session.execute(stmt_fb_m)).scalars().first()
                                fb_ir = float(getattr(fb_m, "info_ratio", 0.0) or 0.0)
                                fb_expect = float(getattr(fb_m, "oos_expect_bp", 0.0) or 0.0)
                                from orion.core.errors import ErrorCode

                                logger.warning(
                                    f"ROUTER FALLBACK TRIGGERED: Using baseline {fallback_id}",
                                    extra={"error_code": ErrorCode.ROUTER_NO_ELIGIBLE_SOLVER.value},
                                )
                                return [
                                    SelectedSolver(
                                        solver_id=fallback_solver.solver_id,
                                        config=fb_cfg,
                                        info_ratio=fb_ir,
                                        oos_expect_bp=fb_expect,
                                        is_ticker_specific=False,
                                        is_baseline=True,
                                    )
                                ]
                            except Exception as e:
                                logger.error(f"Fallback solver {fallback_id} config invalid: {e}")

                    return []

                # Selection Logic: prefer ticker-specific solvers; then rank by metrics score (IR proxy), then stable by version_id.
                ranked_candidates.sort(
                    key=lambda t: (
                        1 if t[0].is_ticker_specific else 0,
                        t[1],
                        t[0].config.version_id,
                    ),
                    reverse=True,
                )

                return [ss for (ss, _) in ranked_candidates[:top_k]]

            except Exception as e:
                from orion.core.errors import ErrorCode

                logger.error(
                    f"Error selecting solvers: {e}", extra={"error_code": ErrorCode.SOLVER_SELECTION_FAILED.value}
                )
                return []
