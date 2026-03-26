from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.processing.signal_engine import SignalEngine
from orion.storage.models_gold import CandidateTrade


@pytest.mark.asyncio
async def test_signal_engine_fallback_when_router_empty():
    """
    Verifies that SignalEngine falls back to a safe default (SKIP)
    with a distinct reason when SolverRouter returns no solvers.
    """
    engine = SignalEngine()

    # Mock Feature Engine
    engine.feature_engine = MagicMock()
    engine.feature_engine.hydrate_history = AsyncMock()
    engine.feature_engine.history = {}
    engine.feature_engine.flow_history = {}

    # Mock the SolverEnsemble stage's router to return empty
    ensemble_stage = engine._stages[2]
    ensemble_stage.router = MagicMock()
    ensemble_stage.router.select_solvers = AsyncMock(return_value=[])

    # Create Candidate
    candidate = CandidateTrade(
        candidate_id="test_cand_1",
        ticker="AAPL",
        timestamp_utc=datetime.now(UTC),
        rule_id="rule_test",
        direction="LONG",
        confidence=1.0,
        evidence={},
    )

    # Execute
    decision = await engine.decide(candidate)

    # Assertions: should SKIP with solver_ensemble stage ID and fallback trace
    assert decision.decision == "SKIP"
    assert decision.strategy_version_id == "SOLVER_ENSEMBLE"
    assert "Fallback" in decision.reason or "Router empty" in decision.reason
    assert decision.decision_trace_json.get("solver_ensemble", {}).get("fallback_triggered") is True
