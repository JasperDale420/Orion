from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orion.processing.signal_engine import SignalEngine

# Mock modules
# import sys
# sys.modules['orion.core.solver_router'] = MagicMock()
# sys.modules['orion.analysis.regime'] = MagicMock()
from orion.storage.models_gold import CandidateTrade


@pytest.mark.asyncio
async def test_signal_engine_regime_integration():
    # Real import of SignalEngine but mocking its dependencies through patch
    with patch("orion.processing.signal_engine.SolverRouter") as MockRouter, patch(
        "orion.processing.signal_engine.RegimeDetector"
    ) as MockDetector:
        mock_router = AsyncMock()
        MockRouter.return_value = mock_router

        mock_detector = AsyncMock()
        MockDetector.return_value = mock_detector

        # Mock Regime Return
        mock_regime_enum = MagicMock()
        mock_regime_enum.value = "HIGH_VOL"
        mock_detector.get_current_regime_for_ticker.return_value = mock_regime_enum

        engine = SignalEngine()

        # Test Data
        candidate = CandidateTrade(
            candidate_id="c1",
            ticker="AAPL",
            timestamp_utc=datetime.now(),
            rule_id="r1",
            direction="LONG",
            confidence=0.9,
            evidence={},
            source="UW",  # Add source because schema requires it or logic uses it
        )

        # Execute
        await engine.decide(candidate)

        # Verify Regulator Called
        mock_detector.get_current_regime_for_ticker.assert_called_with("AAPL")

        # Verify Router Called with Context
        assert mock_router.select_solvers.called
        call_args = mock_router.select_solvers.call_args
        context = call_args[0][0]  # First arg of call

        assert context.ticker == "AAPL"
        assert context.regime == "HIGH_VOL"
