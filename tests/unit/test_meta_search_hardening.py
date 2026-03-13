import pytest
from pydantic import ValidationError

from orion.agents.meta_search_agent import MetaSearchAgent
from orion.core.solver_schema import EditOp, EditOpType, SolverConfig, SolverEdit, SolverRiskConfig
from orion.storage.models_solvers import SolverMetrics


@pytest.fixture
def agent():
    return MetaSearchAgent()


def test_composite_score_calculation(agent):
    # Test safe weighted average
    m = SolverMetrics(sharpe_ratio=2.0, profit_factor=1.5, info_ratio=1.0, stability_score=0.8, max_dd_pct=0.10)
    score = agent._calculate_composite_score(m)
    # Expected: (0.4*2.0) + (0.3*1.5) + (0.2*1.0) + (0.1*0.8) = 0.8 + 0.45 + 0.2 + 0.08 = 1.53
    # DD penalty not triggered
    assert abs(score - 1.53) < 1e-4


def test_composite_score_dd_penalty(agent):
    m = SolverMetrics(
        sharpe_ratio=2.0,
        max_dd_pct=30.0,  # > 25.0 trigger
    )
    score = agent._calculate_composite_score(m)
    # Base: 0.8 (sharpe) + 0 + 0 + 0 = 0.8
    # Penalty: -2.0
    # Expected: -1.2
    assert score < 0.0


def test_validate_config_safe(agent):
    # Just ensure instantiation works
    try:
        SolverConfig(
            version_id="test",
            # base_strategy_name="test",
            entry_logic={"rules": [], "combination_method": "AND", "min_score": 1.0},
            exit_logic={},
            risk=SolverRiskConfig(risk_per_trade_bps=100, max_open_positions=1),
        )
    except ValidationError:
        pytest.fail("Valid config raised ValidationError")


def test_validate_config_unsafe_risk(agent):
    # Risk > 500 bps (5%)
    with pytest.raises(ValidationError):
        SolverConfig(
            version_id="test",
            # base_strategy_name="test",
            entry_logic={"rules": [], "combination_method": "AND", "min_score": 1.0},
            exit_logic={},
            risk=SolverRiskConfig(risk_per_trade_bps=600, max_open_positions=1),
        )


def test_apply_edit_validation_hook(agent):
    base_cfg = SolverConfig(
        version_id="base",
        # base_strategy_name="test",
        entry_logic={"rules": [], "combination_method": "AND", "min_score": 1.0},
        exit_logic={},
        risk=SolverRiskConfig(risk_per_trade_bps=100, max_open_positions=1),
    )

    # Edit that pushes risk to 1000
    edit = SolverEdit(
        base_solver_id="base",
        new_solver_id="new",
        generated_by="test",
        ops=[EditOp(op=EditOpType.MODIFY_RISK, param_name="risk_per_trade_bps", new_value=1000, reasoning="YOLO")],
    )

    with pytest.raises(ValueError):
        agent.apply_edit(base_cfg, edit)
