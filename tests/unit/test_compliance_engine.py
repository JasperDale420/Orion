from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.core.solver_schema import LiveContext
from orion.processing.signal_engine import SignalEngine
from orion.storage.models_gold import CandidateTrade


@pytest.mark.asyncio
async def test_signal_engine_no_v1_fallback():
    """
    Compliance Check: SignalEngine must SKIP if Router returns no solvers.
    It must NOT use legacy confidence-based fallback.
    """
    # Patch dependencies to avoid DB init
    with (
        patch("orion.processing.signal_engine.SolverRouter") as MockRouter,  # noqa: N806
        patch("orion.processing.signal_engine.RegimeDetector") as MockRegime,  # noqa: N806
        patch("orion.processing.signal_engine.SolverPipeline"),
        patch("orion.processing.signal_engine.FeatureEngine") as MockFeatureEngine,  # noqa: N806
    ):
        engine = SignalEngine()

        # Configure Mocks
        mock_router_instance = MockRouter.return_value
        mock_router_instance.select_solvers = AsyncMock(return_value=[])

        mock_regime_instance = MockRegime.return_value
        mock_regime_instance.get_current_regime_for_ticker = AsyncMock(return_value=MagicMock(value="TRENDING"))

        mock_feature_instance = MockFeatureEngine.return_value
        mock_feature_instance.hydrate_history = AsyncMock()

        candidate = CandidateTrade(
            candidate_id="test_cand_1",
            ticker="AAPL",
            timestamp_utc=datetime.now(UTC),
            confidence=0.95,  # High confidence, would trigger V1 fallback previously
            direction="LONG",
            rule_id="test_rule",
            execution_params={"limit_price": 100.0},  # Add exec params to avoid attribute errors if accessed
            evidence={},
        )

        # Run
        decision = await engine.decide(candidate)

        # Assert
        assert decision.decision == "SKIP"
        assert "Fallback" in (decision.reason or "")
        assert decision.strategy_version_id != "V1_FALLBACK"


@pytest.mark.asyncio
async def test_solver_router_strict_regime_filtering():
    """
    Compliance Check: SolverRouter must strictly skip solvers with mismatching regimes,
    including UNKNOWN vs REQUIRED.
    """
    from orion.core.solver_router import SolverRouter
    from orion.storage.models_solvers import Solver

    router = SolverRouter()

    # Setup mock context
    context = LiveContext(
        ticker="AAPL",
        regime="UNKNOWN",  # Current regime is unknown
        time_of_day_utc=datetime.now(UTC),
        current_stage="research",
    )

    # Setup mock active solver that REQUIRES a regime
    config_dict = {
        "version_id": "strict_solver_v1",
        "universe": {"required_regime": "TRENDING"},
        "rules": [],
        "features": {"feature_set_id": "f_v1", "event_features": [], "window_features": []},
    }

    mock_solver = Solver(solver_id="strict_solver_v1", config=config_dict, is_active=True, stage="paper")

    # Mock DB session
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars().all.return_value = [mock_solver]
    mock_session.execute.return_value = mock_result

    with patch("orion.core.solver_router.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__.return_value = mock_session

        selected = await router.select_solvers(context)

        # Should be empty because UNKNOWN != TRENDING
        assert len(selected) == 0
