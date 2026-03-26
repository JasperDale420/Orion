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
    engine = SignalEngine()

    # Mock feature engine to avoid real DB calls
    engine.feature_engine = MagicMock()
    engine.feature_engine.hydrate_history = AsyncMock()
    engine.feature_engine.history = {}
    engine.feature_engine.flow_history = {}

    # Mock the SolverEnsemble stage's router to return no solvers
    ensemble_stage = engine._stages[2]
    ensemble_stage.router = MagicMock()
    ensemble_stage.router.select_solvers = AsyncMock(return_value=[])

    candidate = CandidateTrade(
        candidate_id="test_cand_1",
        ticker="AAPL",
        timestamp_utc=datetime.now(UTC),
        confidence=0.95,  # High confidence, would trigger V1 fallback previously
        direction="LONG",
        rule_id="test_rule",
        execution_params={"limit_price": 100.0},
        evidence={},
    )

    decision = await engine.decide(candidate)

    assert decision.decision == "SKIP"
    assert "Fallback" in (decision.reason or "") or "Router empty" in (decision.reason or "")
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

    context = LiveContext(
        ticker="AAPL",
        regime="UNKNOWN",
        time_of_day_utc=datetime.now(UTC),
        current_stage="research",
    )

    config_dict = {
        "version_id": "strict_solver_v1",
        "universe": {"required_regime": "TRENDING"},
        "rules": [],
        "features": {"feature_set_id": "f_v1", "event_features": [], "window_features": []},
    }

    mock_solver = Solver(solver_id="strict_solver_v1", config=config_dict, is_active=True, stage="paper")

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars().all.return_value = [mock_solver]
    mock_session.execute.return_value = mock_result

    with patch("orion.core.solver_router.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__.return_value = mock_session

        selected = await router.select_solvers(context)

        assert len(selected) == 0
