import logging
from datetime import datetime, timezone

from orion.analysis.regime import MultiAxisRegimeDetector, RegimeDetector
from orion.config import risk_settings
from orion.core.errors import ErrorCode, FeatureComputationError, ModelInferenceError
from orion.core.solver_executor import SolverPipeline
from orion.core.solver_router import SolverRouter
from orion.core.solver_schema import LiveContext
from orion.processing.feature_engine import FeatureEngine
from orion.storage.models_gold import CandidateTrade, StrategyDecision

logger = logging.getLogger(__name__)


class SignalEngine:
    """
    PRD 11.3: Deterministic Decision Policy (hard requirement).
    Consumes CandidateTrades (Gold) from RuleEngine and applies:
    1. Active Solver Retrieval (V2 via SolverRouter)
    2. Solver-Specific Risk/Logic

    Output: StrategyDecision (Signal)
    """

    def __init__(self) -> None:
        # Initialize Router
        self.router = SolverRouter()
        self.regime_detector = RegimeDetector()
        self.multi_axis_detector = MultiAxisRegimeDetector()
        self.pipeline = SolverPipeline()
        # Initialize Singleton Feature Engine
        self.feature_engine = FeatureEngine()
        # Track previous regime per ticker for cache invalidation
        self._previous_regime: dict[str, str] = {}

    async def initialize(self) -> None:
        """
        Initializes sub-components (FeatureEngine hydration).
        """
        logger.info("Initializing SignalEngine...")
        await self.feature_engine.hydrate_history()

    async def decide(self, candidate: CandidateTrade) -> StrategyDecision:
        """
        Applies deterministic policy to a Candidate.
        """

        # 0. Detect Legacy Regime (for backwards compatibility)
        current_regime = await self.regime_detector.get_current_regime_for_ticker(candidate.ticker)
        current_regime_str = current_regime.value if current_regime else "UNKNOWN"

        # 0b. Detect Multi-Axis Regime
        from orion.analysis.regime_risk import RegimeRiskManager

        risk_manager = RegimeRiskManager()

        # Get multi-axis regime snapshot (synchronous detection based on time)
        regime_snapshot = self.multi_axis_detector.detect(
            ts=candidate.timestamp_utc,
        )

        # Check if regime allows trading
        if not risk_manager.should_trade(regime_snapshot):
            return StrategyDecision(
                decision_id=f"regime_skip_{candidate.candidate_id}",
                candidate_id=candidate.candidate_id,
                timestamp_utc=candidate.timestamp_utc,
                ticker=candidate.ticker,
                strategy_version_id="REGIME_FILTER",
                model_version=None,
                decision="SKIP",
                reason=f"Regime SHOCK/blocked: vol={regime_snapshot.vol.value}, vix={regime_snapshot.vix_regime.value}",
                executed_successfully="SKIPPED",
                execution_params={},
                decision_trace_json={
                    "regime_blocked": True,
                    "vol_regime": regime_snapshot.vol.value,
                    "vix_regime": regime_snapshot.vix_regime.value,
                },
            )

        # Calculate regime risk multiplier for later sizing
        regime_size_multiplier = risk_manager.calculate_combined_multiplier(regime_snapshot)

        # Clear feature cache on regime transition (M1 remediation)
        previous_regime = self._previous_regime.get(candidate.ticker)
        if previous_regime and previous_regime != current_regime_str:
            logger.info(
                f"Regime transition detected for {candidate.ticker}: {previous_regime} -> {current_regime_str}. "
                f"Clearing feature cache to avoid stale indicators."
            )
            # Clear cache for this ticker
            if candidate.ticker in self.feature_engine.history:
                del self.feature_engine.history[candidate.ticker]
            if candidate.ticker in self.feature_engine.flow_history:
                del self.feature_engine.flow_history[candidate.ticker]

        # Update regime tracker
        self._previous_regime[candidate.ticker] = current_regime_str

        # 1. Select Solvers via Router (Ensemble)
        from orion.config import system_settings

        stage_env = system_settings.orion_stage

        context = LiveContext(
            ticker=candidate.ticker,
            regime=current_regime_str,
            time_of_day_utc=candidate.timestamp_utc,
            current_stage=stage_env,
        )

        selected_solvers = await self.router.select_solvers(context)

        # Default Decision Record
        decision_record = StrategyDecision(
            decision_id=f"dec_{candidate.candidate_id}",
            candidate_id=candidate.candidate_id,
            timestamp_utc=datetime.now(timezone.utc),
            ticker=candidate.ticker,
            strategy_version_id="V1_LEGACY",  # Default placeholder
            model_version=None,
            decision="SKIP",
            reason="Default: No decision made",
            executed_successfully="PENDING",
            execution_params={},
            decision_trace_json={},
        )

        if selected_solvers:
            # V2 Logic: Ensemble of Active Solvers
            total_weight = 0.0
            weighted_vote = 0.0

            ensemble_details = []
            valid_solver_found = False

            # Leader election (router-ranked primary)
            primary = selected_solvers[0]

            for ss in selected_solvers:
                # Execute Pipeline
                # p_take, weight, trace = await self.pipeline.execute(s, candidate)
                # Catch errors inside pipeline to avoid crashing whole loop?
                try:
                    p_take, _pipeline_weight, trace = await self.pipeline.execute(
                        ss.config, candidate, feature_engine=self.feature_engine
                    )
                except (ModelInferenceError, FeatureComputationError) as e:
                    logger.error(
                        f"Solver {ss.solver_id} FAILED FAST: {e}",
                        extra={
                            "event_type": "SOLVER_EXEC_CRASH",
                            "solver_id": ss.solver_id,
                            "error_code": e.code.value if hasattr(e, "code") else ErrorCode.EXECUTION_FAILED.value,
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

                # PRDv2 FR 5.6.2: weight ∝ info_ratio (capped).
                weight = max(0.0, min(float(ss.info_ratio or 0.0), 5.0))
                # PRDv2 FR 5.6.3: baseline fallback must still be actionable in paper/live.
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
                decision_record.decision = "SKIP"
                decision_record.reason = "No compatible solvers for this rule"
                return decision_record

            consensus_score = weighted_vote / total_weight if total_weight > 0 else 0.0

            # Consensus Threshold (Configurable, default 0.5)
            if consensus_score >= 0.5:
                decision_record.decision = "EXECUTE"
                decision_record.p_take = consensus_score
                decision_record.strategy_version_id = primary.solver_id

                decision_record.reason = f"Ensemble Consensus ({consensus_score:.2f})"

                # Extract Risk Params from Primary Solver
                sl_pct = 0.02  # Default
                tp_pct = 0.04  # Default

                if primary.config.exit_logic:
                    sl_pct = primary.config.exit_logic.fixed_sl_pct or sl_pct
                    tp_pct = primary.config.exit_logic.fixed_tp_pct or tp_pct

                # PRDv2 §11.2 required computed fields.
                expected_return_bp = float(primary.oos_expect_bp or 0.0) * float(consensus_score)
                risk_per_trade_bps = float(primary.config.risk.risk_per_trade_bps) if primary.config.risk else 0.0
                risk_score = (
                    (risk_per_trade_bps / float(risk_settings.max_system_bps))
                    if float(risk_settings.max_system_bps) > 0
                    else 0.0
                )

                decision_record.execution_params = {
                    "limit_price": (
                        candidate.execution_params.get("limit_price") if candidate.execution_params else None
                    ),
                    "stop_loss_pct": sl_pct,
                    "take_profit_pct": tp_pct,
                    "order_type": "LIMIT",
                    "time_in_force": "DAY",
                }

                decision_record.decision_trace_json = {
                    "ensemble_consensus_score": consensus_score,
                    "ensemble_solvers": ensemble_details,
                    "vote_method": "weighted_average_p_take",
                    "weight_method": "info_ratio_capped",
                    "primary_solver": primary.solver_id,
                    "expected_return_bp": expected_return_bp,
                    "risk_score": risk_score,
                    "regime_snapshot": {
                        "trend": regime_snapshot.trend.value,
                        "vol": regime_snapshot.vol.value,
                        "risk": regime_snapshot.risk.value,
                        "session": regime_snapshot.session.value,
                        "vix_regime": regime_snapshot.vix_regime.value,
                    },
                    "regime_size_multiplier": regime_size_multiplier,
                }
            else:
                decision_record.decision = "SKIP"
                # Who said skip?
                decision_record.reason = f"Ensemble Rejected ({consensus_score:.2f} < 0.5)"
        else:
            # Fail-closed but explicit: baseline solver must be configured for PRDv2 fallback.
            logger.warning("Router returned no solvers. Attempting Baseline Fallback.")
            decision_record = self._get_fallback_decision(candidate)

        return decision_record

    def _get_fallback_decision(self, candidate: CandidateTrade) -> StrategyDecision:
        """
        PRD 5.6.3: Fallback Logic.
        If no eligible solvers exist and no baseline can be applied, fail closed (SKIP) but leave a trace.
        """
        from orion.config import system_settings

        return StrategyDecision(
            decision_id=f"fallback_{candidate.candidate_id}",
            candidate_id=candidate.candidate_id,
            timestamp_utc=datetime.now(timezone.utc),
            ticker=candidate.ticker,
            strategy_version_id=system_settings.baseline_solver_id or "FALLBACK_V1",
            model_version=None,
            decision="SKIP",
            reason="Fallback: Router empty and no baseline solver applied; defaulting to safety SKIP",
            executed_successfully="SKIPPED",
            execution_params={},
            decision_trace_json={
                "fallback_triggered": True,
                "baseline_solver_id": system_settings.baseline_solver_id,
            },
        )
