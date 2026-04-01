from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.core.solver_schema import SolverConfig
from orion.processing.signal_engine import SignalEngine
from orion.processing.stages.ml_prefilter import MLPreFilter
from orion.storage.models_gold import CandidateTrade


def test_build_ml_prefilter_payload_normalizes_contract_fields() -> None:
    candidate = CandidateTrade(
        candidate_id="cand_payload",
        ticker="AAPL",
        timestamp_utc=datetime.now(UTC),
        rule_id="rule_test",
        direction="LONG",
        confidence=1.0,
        option_symbol="AAPL260220C00200000",
        option_type="CALL",
        strike_price=200.0,
        premium=2.5,
        underlying_price=198.5,
        execution_params={"put_call": "PUT", "custom_hint": "x"},
        evidence={"premium_usd": 150000, "event_id": "evt_1", "put_call": "P"},
    )

    payload = MLPreFilter._build_payload(candidate)

    assert payload["put_call"] == "C"
    assert payload["strike"] == 200.0
    assert payload["premium_usd"] == 150000
    assert payload["custom_hint"] == "x"


@pytest.mark.asyncio
async def test_ml_prefilter_threshold_reads_centralized_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "orion.processing.stages.ml_prefilter.system_settings.ml_prefilter_threshold", 0.7, raising=False
    )

    engine = SignalEngine()

    candidate = CandidateTrade(
        candidate_id="cand_prefilter",
        ticker="MSFT",
        timestamp_utc=datetime.now(UTC),
        rule_id="rule_test",
        direction="LONG",
        confidence=1.0,
        option_type="CALL",
        premium=1.2,
        strike_price=450.0,
        underlying_price=449.0,
        evidence={"event_id": "evt_prefilter"},
    )

    mock_scorer = MagicMock()
    mock_scorer.score.return_value = 0.6
    mock_scorer.bypass_scoring = False
    mock_scorer.use_heuristic = False  # model-backed scoring uses the configured threshold

    with patch("orion.ml.scorer.get_scorer", return_value=mock_scorer):
        decision = await engine.decide(candidate)

    assert decision.strategy_version_id == "ML_PREFILTER"
    assert decision.decision == "SKIP"
    assert "0.7" in decision.reason


@pytest.mark.asyncio
async def test_ensemble_threshold_reads_centralized_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "orion.processing.stages.solver_ensemble.system_settings.ensemble_consensus_threshold", 0.8, raising=False
    )

    engine = SignalEngine()

    # Mock the SolverEnsemble stage's router to return a solver
    solver = SolverConfig(
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
    solver_obj = MagicMock()
    solver_obj.solver_id = "s1"
    solver_obj.config = solver
    solver_obj.info_ratio = 1.0
    solver_obj.oos_expect_bp = 100
    solver_obj.is_baseline = False

    # Access the SolverEnsemble stage (third stage) and mock its router
    ensemble_stage = engine._stages[2]
    ensemble_stage.router = MagicMock()
    ensemble_stage.router.select_solvers = AsyncMock(return_value=[solver_obj])
    ensemble_stage.pipeline.execute = AsyncMock(return_value=(0.75, 1.0, {}))

    candidate = CandidateTrade(
        candidate_id="cand_consensus",
        ticker="NVDA",
        timestamp_utc=datetime.now(UTC),
        rule_id="rule_test",
        direction="LONG",
        confidence=1.0,
        evidence={"event_id": "evt_consensus"},
    )

    decision = await engine.decide(candidate)

    assert decision.decision == "SKIP"
    assert "0.8" in decision.reason
