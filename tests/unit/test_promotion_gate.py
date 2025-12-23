import pytest
from orion.core.promotion_rules import evaluate_stage_transition
from orion.storage.models_solvers import SolverMetrics


@pytest.mark.asyncio
async def test_promotion_gate_logic():
    """
    Unit-level gate logic checks (PRDv2 §10.5 + promotion_gates.md semantics).
    Workflow/audit persistence is covered elsewhere (Gatekeeper/API).
    """

    promotable = SolverMetrics(
        id="m_prom",
        solver_id="s_prom",
        dataset_tag="test",
        num_trades=150,
        profit_factor=2.0,
        max_dd_pct=1.0,
    )
    assert evaluate_stage_transition(promotable, "research") == "promote"

    demotable = SolverMetrics(
        id="m_dem",
        solver_id="s_dem",
        dataset_tag="live",
        num_trades=50,
        profit_factor=0.8,
        max_dd_pct=5.0,
    )
    assert evaluate_stage_transition(demotable, "paper") == "demote"
