"""
Composable Signal Pipeline.

Defines the protocol and shared context for the signal decision pipeline.
Each stage evaluates a CandidateTrade and either continues or skips.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from orion.analysis.regime import MarketRegimeSnapshot
from orion.storage.models_gold import CandidateTrade


@dataclass
class StageResult:
    """Result of a single pipeline stage evaluation."""

    action: str  # "CONTINUE" or "SKIP"
    reason: str | None = None
    trace: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineContext:
    """Mutable state passed between pipeline stages.

    Each stage reads and/or writes to this context so downstream stages
    have access to upstream results (regime snapshot, ML score, solver
    votes, consensus price, etc.).
    """

    candidate: CandidateTrade

    # Populated by RegimeGate
    regime_snapshot: MarketRegimeSnapshot | None = None
    regime_size_multiplier: float = 1.0
    legacy_regime_str: str = "UNKNOWN"

    # Populated by MLPreFilter
    ml_score: float | None = None

    # Populated by SolverEnsemble
    consensus_score: float = 0.0
    primary_solver_id: str | None = None
    ensemble_details: list[dict[str, Any]] = field(default_factory=list)

    # Execution params built by SolverEnsemble
    limit_price: float | None = None
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.04
    risk_per_trade_bps: float = 100.0
    expected_return_bp: float = 0.0
    risk_score: float = 0.0
    strategy_version_id: str = "V1_LEGACY"


class PipelineStage(Protocol):
    """Protocol for composable signal pipeline stages."""

    @property
    def name(self) -> str:
        """Human-readable stage name for logging and traces."""
        ...

    async def evaluate(self, ctx: PipelineContext) -> StageResult:
        """Evaluate the stage. Returns CONTINUE or SKIP."""
        ...
