import uuid
from datetime import datetime, timezone

import pytest
from orion.config import system_settings
from orion.processing.signal_engine import SignalEngine
from orion.storage.db import async_session_factory
from orion.storage.models_gold import CandidateTrade
from orion.storage.models_solvers import Solver, SolverMetrics


@pytest.mark.asyncio
async def test_baseline_solver_executes_when_selected(monkeypatch):
    # Ensure stage and baseline solver are configured for this test run.
    system_settings.orion_stage = "paper"
    system_settings.baseline_solver_id = "baseline_solver"

    async with async_session_factory() as session:
        session.add(
            Solver(
                solver_id="baseline_solver",
                family_name="Baseline",
                name="Baseline",
                version=1,
                status="active",
                stage="paper",
                is_active=True,
                created_by="human",
                config={"version_id": "baseline_solver"},
                definition_json={"version_id": "baseline_solver"},
            )
        )
        session.add(
            SolverMetrics(
                id=str(uuid.uuid4()),
                solver_id="baseline_solver",
                sector="ALL",
                dataset_tag="test",
                num_runs=1,
                num_trades=1,
                sharpe_ratio=0.0,
                info_ratio=0.0,  # ensure baseline weight bump kicks in
                profit_factor=1.0,
                max_dd_pct=0.0,
                stability_score=0.0,
                metrics_json={},
                oos_expect_bp=10.0,
            )
        )
        await session.commit()

    engine = SignalEngine()

    candidate = CandidateTrade(
        candidate_id="cand_1",
        ticker="SPY",
        timestamp_utc=datetime.now(timezone.utc),
        rule_id="rule_bullish_sweep_v1",
        direction="LONG",
        confidence=0.7,
        source="UW",
        execution_params={"limit_price": 500.0},
        evidence={"event_ids": ["evt_1"], "rule_id": "rule_bullish_sweep_v1"},
    )

    decision = await engine.decide(candidate)
    assert decision.decision == "EXECUTE"
    assert decision.strategy_version_id == "baseline_solver"
    assert decision.p_take is not None and decision.p_take >= 0.5
    assert isinstance(decision.decision_trace_json, dict)
    assert decision.decision_trace_json.get("primary_solver") == "baseline_solver"
