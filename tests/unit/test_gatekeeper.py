from orion.core.promotion_rules import DEFAULT_PROMOTION_CONFIG, evaluate_stage_transition
from orion.storage.models_solvers import SolverMetrics


def test_research_to_shadow_promotion():
    """Verify Research -> Shadow requires sufficient trades and PF."""
    metrics = SolverMetrics(num_trades=105, profit_factor=1.15, max_dd_pct=1.0, num_runs=1)
    action = evaluate_stage_transition(metrics, "research", DEFAULT_PROMOTION_CONFIG)
    assert action == "promote"


def test_research_stay_maintain():
    """Verify Research stays if PF is too low."""
    metrics = SolverMetrics(
        num_trades=105,
        profit_factor=1.05,  # < 1.10
        max_dd_pct=1.0,
    )
    action = evaluate_stage_transition(metrics, "research", DEFAULT_PROMOTION_CONFIG)
    assert action == "maintain"


def test_paper_demotion_on_dd():
    """Verify Paper -> Demote if Max DD violated."""
    metrics = SolverMetrics(
        num_trades=50,
        profit_factor=1.2,
        max_dd_pct=4.0,  # > 3.5 default
    )
    action = evaluate_stage_transition(metrics, "paper", DEFAULT_PROMOTION_CONFIG)
    assert action == "demote"


def test_live_promotion_rules():
    """Verify Limited Live -> Scaled Live."""
    metrics = SolverMetrics(
        num_trades=105,
        profit_factor=1.10,  # > 1.08
        max_dd_pct=1.5,  # < 2.0
    )
    action = evaluate_stage_transition(metrics, "limited_live", DEFAULT_PROMOTION_CONFIG)
    assert action == "promote"
