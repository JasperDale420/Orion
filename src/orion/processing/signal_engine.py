import hashlib
from datetime import UTC, datetime
from typing import Any

from orion.core.enums import DecisionStatus
from orion.processing.feature_engine import FeatureEngine
from orion.processing.pipeline import PipelineContext, PipelineStage, StageResult
from orion.processing.stages.ml_prefilter import MLPreFilter
from orion.processing.stages.regime_gate import RegimeGate
from orion.processing.stages.solver_ensemble import SolverEnsemble
from orion.shared.logger import setup_struct_logger
from orion.storage.models_gold import CandidateTrade, StrategyDecision

logger = setup_struct_logger(__name__)


class SignalEngine:
    """
    PRD 11.3: Deterministic Decision Policy (hard requirement).
    Consumes CandidateTrades (Gold) from RuleEngine and applies a composable
    pipeline of stages: RegimeGate → MLPreFilter → SolverEnsemble.

    Output: StrategyDecision (Signal)
    """

    def __init__(self) -> None:
        # Initialize Feature Engine (shared with SolverEnsemble)
        self.feature_engine = FeatureEngine()

        # Composable pipeline stages
        self._stages: list[PipelineStage] = [
            RegimeGate(),
            MLPreFilter(),
            SolverEnsemble(feature_engine=self.feature_engine),
        ]

        # Track previous regime per ticker for cache invalidation
        self._previous_regime: dict[str, str] = {}

    def process_signals(self, signals: list[Any]) -> list[Any]:
        """
        Backward-compatible adapter for legacy callers/tests.
        Uses legacy FEATURE_EVENT heuristics and falls back to RuleEngine.
        """
        legacy_candidates: list[CandidateTrade] = []
        for signal in signals:
            if getattr(signal, "signal_type", None) != "FEATURE_EVENT":
                continue

            feat = getattr(signal, "features", {}) or {}
            put_call = str(feat.get("call_put") or feat.get("put_call") or "").upper()
            premium = float(feat.get("premium_usd") or 0)
            is_sweep = bool(feat.get("is_sweep"))
            if put_call in {"C", "CALL"} and is_sweep and premium >= 50000:
                ts = signal.signal_ts_utc
                legacy_candidates.append(
                    CandidateTrade(
                        candidate_id=hashlib.sha256(
                            f"{signal.ticker}_{ts.isoformat()}_bullish_sweep_v1".encode()
                        ).hexdigest(),
                        ticker=signal.ticker,
                        timestamp_utc=ts,
                        rule_id="bullish_sweep_v1",
                        direction="LONG",
                        confidence=0.7,
                        evidence={"premium": premium},
                    )
                )

        if legacy_candidates:
            return legacy_candidates

        from orion.processing.rule_engine import RuleEngine

        return RuleEngine().process_signals(signals)

    async def initialize(self) -> None:
        """Initializes sub-components (FeatureEngine hydration)."""
        logger.info("Initializing SignalEngine...")
        await self.feature_engine.hydrate_history()

        logger.info(
            "SignalEngine: market connector unavailable (Alpaca archived), price discovery will use candidate params",
        )

    async def decide(self, candidate: CandidateTrade) -> StrategyDecision:
        """
        Applies deterministic policy to a Candidate via composable pipeline.

        Pipeline: RegimeGate → MLPreFilter → SolverEnsemble
        Each stage returns CONTINUE or SKIP. On SKIP, a StrategyDecision is
        returned immediately. On CONTINUE through all stages, an EXECUTE
        decision is built from the accumulated PipelineContext.
        """
        ctx = PipelineContext(candidate=candidate)
        decision_trace: dict[str, Any] = {}

        # Run each stage in order
        for stage in self._stages:
            try:
                result: StageResult = await stage.evaluate(ctx)
            except Exception as exc:
                logger.error(
                    f"Pipeline stage '{stage.name}' raised an exception: {exc}",
                    extra={"stage": stage.name, "candidate_id": candidate.candidate_id},
                    exc_info=True,
                )
                decision_trace[stage.name] = {"error": str(exc)}
                return self._build_skip_decision(
                    candidate, f"Stage error: {stage.name}: {exc}", stage.name, decision_trace
                )
            decision_trace[stage.name] = result.trace

            if result.action == "SKIP":
                return self._build_skip_decision(candidate, result.reason, stage.name, decision_trace)

        # Feature cache invalidation on regime transition
        self._handle_regime_transition(candidate, ctx)

        # All stages passed — build EXECUTE decision
        return self._build_execute_decision(candidate, ctx, decision_trace)

    def _handle_regime_transition(self, candidate: CandidateTrade, ctx: PipelineContext) -> None:
        """Clear feature cache when regime transitions to avoid stale indicators."""
        current_regime_str = ctx.regime_snapshot.trend.value if ctx.regime_snapshot else "UNKNOWN"
        previous_regime = self._previous_regime.get(candidate.ticker)

        if previous_regime and previous_regime != current_regime_str:
            logger.info(
                f"Regime transition detected for {candidate.ticker}: {previous_regime} -> {current_regime_str}. "
                f"Clearing feature cache to avoid stale indicators."
            )
            if candidate.ticker in self.feature_engine.history:
                del self.feature_engine.history[candidate.ticker]
            if candidate.ticker in self.feature_engine.flow_history:
                del self.feature_engine.flow_history[candidate.ticker]

        self._previous_regime[candidate.ticker] = current_regime_str

    @staticmethod
    def _build_skip_decision(
        candidate: CandidateTrade,
        reason: str | None,
        stage_name: str,
        decision_trace: dict[str, Any],
    ) -> StrategyDecision:
        return StrategyDecision(
            decision_id=f"{stage_name}_skip_{candidate.candidate_id}",
            candidate_id=candidate.candidate_id,
            timestamp_utc=candidate.timestamp_utc,
            ticker=candidate.ticker,
            strategy_version_id=stage_name.upper(),
            model_version=None,
            decision="SKIP",
            reason=reason or f"Rejected by {stage_name}",
            executed_successfully=DecisionStatus.SKIPPED,
            execution_params={},
            decision_trace_json=decision_trace,
        )

    @staticmethod
    def _build_execute_decision(
        candidate: CandidateTrade,
        ctx: PipelineContext,
        decision_trace: dict[str, Any],
    ) -> StrategyDecision:
        # Add regime snapshot to trace
        if ctx.regime_snapshot:
            decision_trace["regime_snapshot"] = {
                "trend": ctx.regime_snapshot.trend.value,
                "vol": ctx.regime_snapshot.vol.value,
                "risk": ctx.regime_snapshot.risk.value,
                "session": ctx.regime_snapshot.session.value,
                "vix_regime": ctx.regime_snapshot.vix_regime.value,
            }
        decision_trace["regime_size_multiplier"] = ctx.regime_size_multiplier

        # PRDv2 §11.2: expected_return_bp and risk_score must be at the top
        # level of decision_trace_json for save_decision validation and
        # downstream signal persistence.
        decision_trace["expected_return_bp"] = ctx.expected_return_bp
        decision_trace["risk_score"] = ctx.risk_score

        return StrategyDecision(
            decision_id=f"dec_{candidate.candidate_id}",
            candidate_id=candidate.candidate_id,
            timestamp_utc=datetime.now(UTC),
            ticker=candidate.ticker,
            strategy_version_id=ctx.strategy_version_id,
            model_version=None,
            decision="EXECUTE",
            p_take=ctx.consensus_score,
            reason=f"Ensemble Consensus ({ctx.consensus_score:.2f})",
            executed_successfully=DecisionStatus.PENDING,
            execution_params={
                "limit_price": ctx.limit_price,
                "stop_loss_pct": ctx.stop_loss_pct,
                "take_profit_pct": ctx.take_profit_pct,
                "risk_per_trade_bps": ctx.risk_per_trade_bps,
                "regime_size_multiplier": ctx.regime_size_multiplier,
                "order_type": "LIMIT",
                "time_in_force": "DAY",
            },
            decision_trace_json=decision_trace,
        )
