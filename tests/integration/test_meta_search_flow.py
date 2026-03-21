from datetime import datetime
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest


@pytest.mark.asyncio
async def test_meta_search_flow():
    """
    End-to-End test for Meta-Search Orchestrator.
    Verifies: Base Loading -> Variant Mutation (SolverEdit) -> Evaluation (Mock Data) -> Persistence (Metrics/Edits).
    """

    # 1. Setup In-Memory DB & Patching
    from sqlalchemy import delete, select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    # Setup mocked engine
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

    # Import Module Purposefully to patch it
    import orion.storage.db

    orion.storage.db.engine = test_engine
    orion.storage.db.async_session_factory = test_session_factory

    # Load/Reload Core Modules to consume patched DB
    # Load/Reload Core Modules to consume patched DB
    # Now import objects from the verified environment
    import orion.agents.meta_search_agent
    from orion.agents.meta_search_agent import MetaSearchAgent
    from orion.storage.db import Base
    from orion.storage.models_gold import CandidateTrade
    from orion.storage.models_solvers import MetaExperiment, Solver, SolverEdits, SolverMetrics

    orion.agents.meta_search_agent.async_session_factory = test_session_factory

    # Also patch SolverRouter if used internally (agent uses Agent -> MetaAgent -> ...?)
    # MetaSearchAgent uses internal session.

    from orion.core.solver_schema import EditOp, EditOpType, SolverConfig, SolverEdit

    # Create Tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    agent = MetaSearchAgent()

    # Mock MetaAgent response
    mock_edits = [
        SolverEdit(
            base_solver_id="base_v1_test",
            new_solver_id="gen_mock",
            generated_by="meta_agent",
            ops=[
                EditOp(
                    op=EditOpType.MODIFY_PARAM,
                    param_name="exit_logic.take_profit_atr_multiple",
                    new_value=2.5,
                    reasoning="More profit",
                )
            ],
        )
    ]
    agent.meta_agent.propose_edits = AsyncMock(return_value=mock_edits)

    # Seed Data (Base Solver + Eval Data)
    base_id = "base_v1_test"

    config = SolverConfig(
        version_id=base_id,
        base_strategy_name="TrendTest",
        entry_logic={"rules": [], "combination_method": "AND"},
        exit_logic={"take_profit_atr_multiple": 2.0, "stop_loss_atr_multiple": 1.0},
        risk_per_trade_bps=100,
    )

    async with test_session_factory() as session:
        # Cleanup (not strictly needed in memory but good practice)
        await session.execute(delete(Solver))
        await session.execute(delete(SolverEdits))
        await session.execute(delete(SolverMetrics))
        await session.execute(delete(MetaExperiment))
        await session.execute(delete(CandidateTrade))

        # Insert Base Solver
        base_solver = Solver(
            solver_id=base_id,
            family_name="TrendTest",
            config=config.model_dump(mode="json"),
            is_active=True,
            sharpe_ratio=1.5,
        )
        session.add(base_solver)

        # Insert Mock Eval Data (candidate only; event context is sourced from Heber adapters)
        cand = CandidateTrade(
            candidate_id="mock_c_eval",
            ticker="SPY",
            timestamp_utc=datetime.utcnow(),
            rule_id="r1",
            direction="LONG",
            evidence={},
        )
        session.add(cand)

        await session.commit()

    print("Starting Evolution...")
    # Patch fetch_events_from_heber so the test does not attempt live Parquet I/O against
    # /Volumes/heber.  The test verifies orchestration logic (experiment creation,
    # SolverEdit persistence, metrics storage) — not data ingestion.  Returning empty
    # tuples causes evaluate_variant to take the fast "no_data" path, which still
    # produces a SolverRun + SolverMetrics row (sharpe=0, note="no_data").
    with patch(
        "orion.agents.meta_search_agent.fetch_events_from_heber",
        new=AsyncMock(return_value=([], [], {})),
    ):
        await agent.run_evolution_cycle(base_id, experiment_name="IntegrationTest")
    print("Evolution Complete.")

    # 3. Verify Results
    async with test_session_factory() as session:
        # Check Experiment
        stmt_exp = select(MetaExperiment).where(MetaExperiment.description == "IntegrationTest")
        exp = (await session.execute(stmt_exp)).scalars().first()
        assert exp is not None, "Experiment not found"
        assert exp.status == "completed", f"Experiment status {exp.status} != completed"

        print(f"VERIFY: Experiment Status: {exp.status}")

        # Check Edits
        stmt_edits = select(SolverEdits).where(SolverEdits.experiment_id == exp.experiment_id)
        edits = (await session.execute(stmt_edits)).scalars().all()
        assert len(edits) > 0, "No edits generated"
        print(f"VERIFY: Edits Count: {len(edits)}")

        assert edits[0].generated_by == "meta_agent"
        print(f"VERIFY: Edit Generated By: {edits[0].generated_by}")

        # Check Metrics
        stmt_metrics = select(SolverMetrics).where(SolverMetrics.solver_id == exp.best_solver_id)
        metrics = (await session.execute(stmt_metrics)).scalars().first()
        assert metrics is not None, "Best Solver Metrics not found"

        print(f"VERIFY: Best Solver Sharpe: {metrics.sharpe_ratio}")
