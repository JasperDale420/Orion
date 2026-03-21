from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.analysis.regime import MarketRegime
from orion.core.solver_schema import SolverConfig
from orion.processing.signal_engine import SignalEngine
from orion.storage.models_gold import CandidateTrade


@pytest.mark.asyncio
async def test_ensemble_decision_consensus():
    # Mock Router to return 2 solvers
    mock_router = AsyncMock()

    solver_a = SolverConfig(
        solver_id="s1",
        version_id="v1_agile",
        base_strategy_name="AgileStrategy",
        rules=["rule_sweep"],
        features={"event_features": []},
        model={},
        risk={},
        entry_logic={"rules": [], "combination_method": "AND"},
        exit_logic={"fixed_sl_pct": 0.02, "fixed_tp_pct": 0.05},
        execution={},
        promotion_policy={},
        stage="paper",
    )
    solver_b = SolverConfig(
        solver_id="s2",
        version_id="v2_conservative",
        base_strategy_name="ConservativeStrategy",
        rules=["rule_sweep"],
        features={"event_features": []},
        model={},
        risk={},
        entry_logic={"rules": [], "combination_method": "AND"},
        exit_logic={"fixed_sl_pct": 0.01, "fixed_tp_pct": 0.02},
        execution={},
        promotion_policy={},
        stage="paper",
    )

    s1_obj = MagicMock()
    s1_obj.solver_id = "s1"
    s1_obj.config = solver_a

    s2_obj = MagicMock()
    s2_obj.solver_id = "s2"
    s2_obj.config = solver_b

    mock_router.select_solvers.return_value = [s1_obj, s2_obj]

    with patch("orion.processing.signal_engine.SolverRouter", return_value=mock_router):
        engine = SignalEngine()
        # Mock Regime
        engine.regime_detector.get_current_regime_for_ticker = AsyncMock(return_value=MarketRegime.TRENDING_UP)

        # Mock Pipeline Execution
        # Solver A says Take (0.8), Solver B says Take (0.7) -> Consensus > 0.5
        engine.pipeline.execute = AsyncMock(side_effect=[(0.8, 1.0, {"trace": "A"}), (0.7, 1.0, {"trace": "B"})])

        candidate = CandidateTrade(
            candidate_id="c1",
            source="UW",
            ticker="AAPL",
            timestamp_utc=datetime.now(UTC),
            rule_id="rule_sweep",
            confidence=0.5,
            direction="LONG",
            evidence={"event_id": "e1"},  # Required field
        )

        decision = await engine.decide(candidate)

        assert decision.decision == "EXECUTE"
        assert "Ensemble Consensus" in decision.reason
        assert decision.strategy_version_id == "s1"  # Leader elected
        assert decision.decision_trace_json["ensemble_consensus_score"] == 0.75  # (0.8+0.7)/2


@pytest.mark.asyncio
async def test_ensemble_decision_rejection():
    mock_router = AsyncMock()
    solver_a = SolverConfig(
        solver_id="s1",
        version_id="v1",
        base_strategy_name="Base",
        rules=[],
        features={},
        model={},
        risk={},
        entry_logic={"rules": [], "combination_method": "AND"},
        exit_logic={},
        execution={},
        promotion_policy={},
        stage="paper",
    )

    s1_obj = MagicMock()
    s1_obj.solver_id = "s1"
    s1_obj.config = solver_a

    # Single solver, returns low prob
    mock_router.select_solvers.return_value = [s1_obj]

    with patch("orion.processing.signal_engine.SolverRouter", return_value=mock_router):
        engine = SignalEngine()
        engine.regime_detector.get_current_regime_for_ticker = AsyncMock(return_value=MarketRegime.TRENDING_DOWN)

        # Pipeline returns 0.2
        engine.pipeline.execute = AsyncMock(return_value=(0.2, 1.0, {}))

        candidate = CandidateTrade(
            candidate_id="c2",
            source="UW",
            ticker="TSLA",
            timestamp_utc=datetime.now(UTC),
            rule_id="rule_sweep",
            confidence=0.5,
            direction="SHORT",
            evidence={"event_id": "e2"},
        )

        decision = await engine.decide(candidate)

        assert decision.decision == "SKIP"
        assert "Ensemble Rejected" in decision.reason
