from datetime import datetime, timezone

import pytest
from orion.core.solver_executor import SolverPipeline
from orion.core.solver_schema import ExitLogic, SolverConfig
from orion.storage.models_gold import CandidateTrade as DBCandidateTrade

# Mock CandidateTrade if DB model is complex or needs deps,
# but we can try using the real pydantic model if available.
# The code uses 'orion.storage.models_gold.CandidateTrade'
# Let's check imports in usage.


@pytest.mark.asyncio
async def test_solver_deterministic_execution():
    pipeline = SolverPipeline()

    # Setup Solver Config
    solver_config = SolverConfig(
        version_id="test_v1",
        # base_strategy_name="test_strat", # This field doesn't exist in schema anymore? check schema
        # Schema has: version_id, rules, features, model, risk, universe, exit_logic...
        # It does NOT have base_strategy_name?
        # Let's check valid fields from schema view earlier.
        # It has NO base_strategy_name.
        # It has rules=[].
        # entry_logic is Optional[Dict].
        rules=[],
        entry_logic={"rules": []},
        exit_logic=ExitLogic(),
    )

    # Setup Candidate
    candidate = DBCandidateTrade(
        candidate_id="c1",
        timestamp_utc=datetime.now(timezone.utc),
        ticker="SPY_TEST",
        direction="LONG",
        rule_id="rule_1",
        confidence=0.75,
        evidence={},
    )

    # We need to mock FeatureEngine behavior because Pipeline instantiates it internally.
    # In a real heavy test we'd patch 'orion.core.solver_executor.FeatureEngine'
    # For now, let's assume FeatureEngine returns Empty if no data,
    # and Pipeline falls back to confidence.

    # Run 1
    p1, w1, t1 = await pipeline.execute(solver_config, candidate)

    # Run 2
    p2, w2, t2 = await pipeline.execute(solver_config, candidate)

    # Assert Equality (Zero Variance)
    assert p1 == p2
    assert "stage" in t1
    assert t1["stage"] == "model_inference_deterministic"
    assert p1 == 0.75  # Default fallback

    # Now verify randomness is GONE (if we had the old random.uniform it would fail this equality often)
