from unittest.mock import AsyncMock

import pytest
from orion.agents.meta_search_agent import MetaSearchAgent
from orion.core.solver_schema import SolverConfig
from orion.storage.models_solvers import Solver


@pytest.mark.asyncio
async def test_heuristic_fallback():
    """
    Verifies that MetaSearchAgent falls back to heuristic generation when LLM fails,
    and produces sensible edits based on performance (simulated).
    """
    agent = MetaSearchAgent()

    # Mock MetaAgent (LLM) to return empty list (Failure)
    agent.meta_agent.propose_edits = AsyncMock(return_value=[])

    # Setup Base Solver Config
    base_config_dict = {
        "version_id": "v1",
        "base_strategy_name": "TestStrat",
        "timeframe": "5m",
        "entry_logic": {"rules": [], "combination_method": "AND", "min_score": 1.0},
        "exit_logic": {"take_profit_atr_multiple": 2.0, "stop_loss_atr_multiple": 1.0, "time_limit_bars": 12},
        "risk": {"risk_per_trade_bps": 100, "max_open_positions": 1},
    }

    # 1. Test "Struggling" Scenario (Sharpe < 0.5)
    # Heuristic should TIGHTEN risk (Reduce risk_per_trade_bps)
    struggling_metrics = Solver(
        solver_id="v1",
        config=base_config_dict,
        sharpe_ratio=0.2,  # low
    )
    base_config = SolverConfig(**base_config_dict)

    edits = agent._generate_heuristic_variants(base_config, struggling_metrics, count=1, generated_by="test_fallback")

    assert len(edits) > 0
    first_edit = edits[0]
    assert first_edit.generated_by == "test_fallback"

    # Check ops
    has_risk_reduction = False
    for op in first_edit.ops:
        if op.param_name == "risk_per_trade_bps":
            # 100 * 0.8 = 80
            if op.new_value < 100:
                has_risk_reduction = True

    assert has_risk_reduction, "Heuristic failed to reduce risk for struggling strategy"

    # 2. Test "Performing" Scenario (Sharpe > 0.5)
    # Heuristic should INCREASE risk or Loosen TP
    performing_metrics = Solver(solver_id="v1", config=base_config_dict, sharpe_ratio=1.5)

    edits_good = agent._generate_heuristic_variants(
        base_config, performing_metrics, count=1, generated_by="test_fallback"
    )
    first_edit_gold = edits_good[0]

    has_risk_increase = False
    for op in first_edit_gold.ops:
        if op.param_name == "risk_per_trade_bps":
            if op.new_value > 100:
                has_risk_increase = True

    assert has_risk_increase, "Heuristic failed to increase risk for performing strategy"
