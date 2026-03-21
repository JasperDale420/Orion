from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.processing.signal_engine import SignalEngine
from orion.storage.models_gold import CandidateTrade


@pytest.mark.asyncio
async def test_signal_engine_fallback_when_router_empty():
    """
    Verifies that SignalEngine falls back to a safe default (SKIP)
    with a distinct reason/strategy_id when SolverRouter returns no solvers.
    """
    engine = SignalEngine()

    # Mock Router
    engine.router = MagicMock()
    engine.router.select_solvers = AsyncMock(return_value=[])

    # Mock Feature Engine
    engine.feature_engine = MagicMock()
    engine.feature_engine.hydrate_history = AsyncMock()

    # Mock Regime Detector (Prevents DB Call)
    engine.regime_detector = MagicMock()
    engine.regime_detector.get_current_regime_for_ticker = AsyncMock(return_value=None)

    # Mock Pipeline (Prevents DB/Logic)
    engine.pipeline = MagicMock()

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
    with patch("orion.config.system_settings") as mock_settings:
        mock_settings.baseline_solver_id = None
        mock_settings.orion_stage = "paper"
        decision = await engine.decide(candidate)

    # Assertions
    assert decision.decision == "SKIP"
    # We expect the new fallback behavior to set a specific ID or reason
    # Current behavior (before fix) might be "No active solver selected"
    # We want to change it to use a reliable Fallback method that could hypothetically be swapped for a safe solver later.

    # Checking for our PLANNED behavior:
    # If this fails, it means we haven't implemented the fix yet (RED phase)
    # The current code sets reason="No active solver selected (Router returned empty)"
    # We want to refactor to _get_fallback_decision which might be cleaner or standardized.

    # Let's assert what we WANT after the fix:
    assert decision.strategy_version_id == "FALLBACK_V1"
    assert "Fallback" in decision.reason
    assert decision.decision_trace_json.get("fallback_triggered") is True
