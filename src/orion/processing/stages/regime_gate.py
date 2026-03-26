"""
RegimeGate Pipeline Stage.

Detects multi-axis market regime and blocks trading during SHOCK conditions.
"""

from __future__ import annotations

from orion.analysis.regime import MultiAxisRegimeDetector
from orion.analysis.regime_risk import RegimeRiskManager
from orion.processing.pipeline import PipelineContext, StageResult
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.processing.stages.regime_gate")


class RegimeGate:
    """Block trading during SHOCK regimes and compute sizing multiplier."""

    def __init__(self) -> None:
        self.multi_axis_detector = MultiAxisRegimeDetector()
        self.risk_manager = RegimeRiskManager()

    @property
    def name(self) -> str:
        return "regime_gate"

    async def evaluate(self, ctx: PipelineContext) -> StageResult:
        candidate = ctx.candidate

        # Detect multi-axis regime snapshot
        regime_snapshot = self.multi_axis_detector.detect(
            ts=candidate.timestamp_utc,
        )
        ctx.regime_snapshot = regime_snapshot

        # Check if regime allows trading
        if not self.risk_manager.should_trade(regime_snapshot):
            return StageResult(
                action="SKIP",
                reason=f"Regime SHOCK/blocked: vol={regime_snapshot.vol.value}, vix={regime_snapshot.vix_regime.value}",
                trace={
                    "regime_blocked": True,
                    "vol_regime": regime_snapshot.vol.value,
                    "vix_regime": regime_snapshot.vix_regime.value,
                },
            )

        # Compute regime sizing multiplier for downstream use
        ctx.regime_size_multiplier = self.risk_manager.calculate_combined_multiplier(regime_snapshot)

        return StageResult(
            action="CONTINUE",
            trace={
                "regime_snapshot": {
                    "trend": regime_snapshot.trend.value,
                    "vol": regime_snapshot.vol.value,
                    "risk": regime_snapshot.risk.value,
                    "session": regime_snapshot.session.value,
                    "vix_regime": regime_snapshot.vix_regime.value,
                },
                "regime_size_multiplier": ctx.regime_size_multiplier,
            },
        )
