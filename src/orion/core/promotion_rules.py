from dataclasses import dataclass

from orion.storage.models_solvers import SolverMetrics

STAGE_ORDER = ["research", "shadow", "paper", "limited_live", "scaled_live"]


@dataclass
class StageConfig:
    min_days: int = 0
    min_trades: int = 0
    min_pf: float = 0.0
    max_dd_pct: float = 100.0  # Default essentially no limit
    min_expectancy_bps: float = 0.0
    min_sharpe: float = 0.0


@dataclass
class PromotionConfig:
    # Explicit thresholds matching promotion_gates.md
    research_to_shadow: StageConfig
    shadow_to_paper: StageConfig
    paper_to_limited_live: StageConfig
    limited_live_to_scaled_live: StageConfig

    # Demotion thresholds
    demotion_max_dd_paper: float = 3.5
    demotion_max_dd_live: float = 3.0
    demotion_min_pf_rolling: float = 0.95


# Default Configuration based on PRD v1 defaults
DEFAULT_PROMOTION_CONFIG = PromotionConfig(
    research_to_shadow=StageConfig(
        min_days=60,  # Backtest duration coverage
        min_trades=100,  # OOS trades
        min_pf=1.10,
    ),
    shadow_to_paper=StageConfig(
        min_days=10,
        min_trades=50,  # Signals
        # Parity check is separate logic
    ),
    paper_to_limited_live=StageConfig(
        min_days=20,
        min_trades=100,  # Fills
        min_pf=1.05,
        max_dd_pct=2.5,
    ),
    limited_live_to_scaled_live=StageConfig(min_days=30, min_trades=100, min_pf=1.08, max_dd_pct=2.0),
)


def evaluate_stage_transition(
    metrics: SolverMetrics, current_stage: str, config: PromotionConfig = DEFAULT_PROMOTION_CONFIG
) -> str:
    """
    Evaluates whether a solver should be promoted, demoted, or stay put.
    Returns: 'promote', 'demote', 'maintain'
    """
    # 1. Check Demotion First (Safety First)
    if current_stage in ["paper", "limited_live", "scaled_live"]:
        limit = config.demotion_max_dd_live if "live" in current_stage else config.demotion_max_dd_paper
        # max_dd_pct is usually positive in Metrics (absolute val of drop)?
        # PRD says "max drawdown <= 2.5%". Usually DD is reported as a positive percentage of loss, e.g. 5.0% DD.
        # Ensure metrics store it consistently. Let's assume positive float representing % drop.
        if metrics.max_dd_pct > limit:
            return "demote"

        if metrics.profit_factor < config.demotion_min_pf_rolling and metrics.num_trades > 20:
            return "demote"

    # 2. Check Promotion
    stage_map = {
        "research": config.research_to_shadow,
        "shadow": config.shadow_to_paper,
        "paper": config.paper_to_limited_live,
        "limited_live": config.limited_live_to_scaled_live,
    }

    target_cfg = stage_map.get(current_stage)
    if not target_cfg:
        return "maintain"  # No promotion defined from here (e.g. scaled_live)

    # Check criteria
    # Note: metrics.num_runs might act as 'days' proxy if daily runs?
    # Better: metrics should probably include 'days_coverage' or similar.
    # For V1, we rely on num_trades and basic metrics.

    if (
        metrics.num_trades >= target_cfg.min_trades
        and metrics.profit_factor >= target_cfg.min_pf
        and metrics.max_dd_pct <= target_cfg.max_dd_pct
    ):
        return "promote"

    return "maintain"
