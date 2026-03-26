"""
SolverEnsemble Pipeline Stage.

Selects solvers via SolverRouter, executes each through the SolverPipeline,
and computes a weighted consensus score for trade decisions.
"""

from __future__ import annotations

from orion.config import risk_settings, system_settings
from orion.core.errors import ErrorCode, FeatureComputationError, ModelInferenceError
from orion.core.solver_executor import SolverPipeline
from orion.core.solver_router import SolverRouter
from orion.core.solver_schema import LiveContext
from orion.processing.feature_engine import FeatureEngine
from orion.processing.pipeline import PipelineContext, StageResult
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.processing.stages.solver_ensemble")


class SolverEnsemble:
    """Select solvers, execute pipelines, and vote on trade decisions."""

    def __init__(self, feature_engine: FeatureEngine | None = None) -> None:
        self.router = SolverRouter()
        self.pipeline = SolverPipeline()
        self.feature_engine = feature_engine or FeatureEngine()

    @property
    def name(self) -> str:
        return "solver_ensemble"

    async def evaluate(self, ctx: PipelineContext) -> StageResult:
        candidate = ctx.candidate
        stage_env = system_settings.orion_stage

        # Build routing context
        live_context = LiveContext(
            ticker=candidate.ticker,
            regime=ctx.legacy_regime_str,
            time_of_day_utc=candidate.timestamp_utc,
            current_stage=stage_env,
        )

        selected_solvers = await self.router.select_solvers(live_context)

        if not selected_solvers:
            # Fail-closed: no solvers available
            logger.warning("Router returned no solvers. Attempting Baseline Fallback.")
            return StageResult(
                action="SKIP",
                reason="Fallback: Router empty and no baseline solver applied; defaulting to safety SKIP",
                trace={
                    "fallback_triggered": True,
                    "baseline_solver_id": system_settings.baseline_solver_id,
                },
            )

        # Execute each solver's pipeline and collect weighted votes
        total_weight = 0.0
        weighted_vote = 0.0
        ensemble_details: list[dict] = []
        valid_solver_found = False

        # Leader election (router-ranked primary)
        primary = selected_solvers[0]

        for ss in selected_solvers:
            try:
                p_take, _pipeline_weight, trace = await self.pipeline.execute(
                    ss.config, candidate, feature_engine=self.feature_engine
                )
            except (ModelInferenceError, FeatureComputationError) as e:
                error_code = getattr(e, "code", ErrorCode.EXECUTION_FAILED)
                if hasattr(error_code, "value"):
                    error_code_value = error_code.value
                else:
                    error_code_value = str(error_code)

                logger.error(
                    f"Solver {ss.solver_id} FAILED FAST: {e}",
                    extra={
                        "event_type": "SOLVER_EXEC_CRASH",
                        "solver_id": ss.solver_id,
                        "error_code": error_code_value,
                    },
                )
                continue
            except Exception as e:
                logger.error(
                    f"Solver {ss.solver_id} failed execution: {e}",
                    extra={
                        "event_type": "SOLVER_EXEC_ERROR",
                        "solver_id": ss.solver_id,
                        "error_code": ErrorCode.EXECUTION_FAILED.value,
                    },
                )
                continue

            # Weight proportional to info ratio (capped at 5.0)
            weight = max(0.0, min(float(ss.info_ratio or 0.0), 5.0))
            # Baseline fallback must still be actionable
            if getattr(ss, "is_baseline", False):
                weight = max(1.0, weight)
            if weight <= 0.0:
                continue

            valid_solver_found = True
            weighted_vote += p_take * weight
            total_weight += weight

            ensemble_details.append(
                {
                    "solver_id": ss.solver_id,
                    "info_ratio": float(ss.info_ratio or 0.0),
                    "oos_expect_bp": float(ss.oos_expect_bp or 0.0),
                    "weight": weight,
                    "p_take": p_take,
                    "trace": trace,
                }
            )

        if not valid_solver_found:
            return StageResult(
                action="SKIP",
                reason="No compatible solvers for this rule",
                trace={"ensemble_details": ensemble_details},
            )

        consensus_score = weighted_vote / total_weight if total_weight > 0 else 0.0
        ensemble_threshold = system_settings.ensemble_consensus_threshold

        if consensus_score < ensemble_threshold:
            return StageResult(
                action="SKIP",
                reason=f"Ensemble Rejected ({consensus_score:.2f} < {ensemble_threshold})",
                trace={
                    "ensemble_consensus_score": consensus_score,
                    "ensemble_threshold": ensemble_threshold,
                    "ensemble_solvers": ensemble_details,
                },
            )

        # Consensus reached — populate context for decision building
        ctx.consensus_score = consensus_score
        ctx.primary_solver_id = primary.solver_id
        ctx.ensemble_details = ensemble_details
        ctx.strategy_version_id = primary.solver_id

        # Extract risk params from primary solver
        sl_pct = risk_settings.default_stop_loss_pct
        tp_pct = 0.04

        if primary.config.exit_logic:
            sl_pct = primary.config.exit_logic.fixed_sl_pct or sl_pct
            tp_pct = primary.config.exit_logic.fixed_tp_pct or tp_pct

        ctx.stop_loss_pct = sl_pct
        ctx.take_profit_pct = tp_pct

        # Solver-level risk params
        if primary.config.risk:
            ctx.risk_per_trade_bps = float(primary.config.risk.risk_per_trade_bps)

        # Expected return and risk score
        ctx.expected_return_bp = float(primary.oos_expect_bp or 0.0) * float(consensus_score)
        max_system_bps = float(risk_settings.max_system_bps)
        ctx.risk_score = (ctx.risk_per_trade_bps / max_system_bps) if max_system_bps > 0 else 0.0

        # Resolve limit price from candidate
        limit_price = None
        if candidate.execution_params:
            limit_price = candidate.execution_params.get("limit_price")
        ctx.limit_price = limit_price

        return StageResult(
            action="CONTINUE",
            trace={
                "ensemble_consensus_score": consensus_score,
                "ensemble_solvers": ensemble_details,
                "vote_method": "weighted_average_p_take",
                "weight_method": "info_ratio_capped",
                "ensemble_threshold": ensemble_threshold,
                "primary_solver": primary.solver_id,
                "expected_return_bp": ctx.expected_return_bp,
                "risk_score": ctx.risk_score,
            },
        )
