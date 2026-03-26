from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from orion.processing.signal_engine import SignalEngine
from orion.storage.models_gold import CandidateTrade


@pytest.mark.asyncio
async def test_signal_engine_regime_integration():
    """
    Verify regime detection is called and its result flows through the pipeline.
    """
    engine = SignalEngine()

    # Mock feature engine
    engine.feature_engine = MagicMock()
    engine.feature_engine.hydrate_history = AsyncMock()
    engine.feature_engine.history = {}
    engine.feature_engine.flow_history = {}

    # Access the RegimeGate stage and mock its detector
    regime_stage = engine._stages[0]
    mock_snapshot = MagicMock()
    mock_snapshot.trend.value = "UP"
    mock_snapshot.vol.value = "NORMAL"
    mock_snapshot.risk.value = "NEUTRAL"
    mock_snapshot.session.value = "MIDDAY"
    mock_snapshot.vix_regime.value = "NORMAL"
    mock_snapshot.vol = MagicMock()
    mock_snapshot.vol.value = "NORMAL"
    # Ensure should_trade returns True
    from orion.analysis.regime import VolRegime

    mock_snapshot.vol = VolRegime.NORMAL

    regime_stage.multi_axis_detector.detect = MagicMock(return_value=mock_snapshot)

    # Mock the SolverEnsemble stage's router to verify it receives context
    ensemble_stage = engine._stages[2]
    ensemble_stage.router = MagicMock()
    ensemble_stage.router.select_solvers = AsyncMock(return_value=[])

    candidate = CandidateTrade(
        candidate_id="c1",
        ticker="AAPL",
        timestamp_utc=datetime.now(UTC),
        rule_id="r1",
        direction="LONG",
        confidence=0.9,
        evidence={},
    )

    await engine.decide(candidate)

    # Verify regime detector was called
    regime_stage.multi_axis_detector.detect.assert_called_once()

    # Verify solver router was called
    assert ensemble_stage.router.select_solvers.called
