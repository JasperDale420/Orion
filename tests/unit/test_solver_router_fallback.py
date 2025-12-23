import datetime
from unittest.mock import patch

import pytest
from orion.core.solver_router import SolverRouter
from orion.core.solver_schema import LiveContext
from orion.storage.db import Base
from orion.storage.models_solvers import Solver, SolverMetrics
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.mark.asyncio
async def test_select_solvers_fallback():
    # 1. Setup In-Memory DB
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

    # 2. Patch Global DB Session (Required for SolverRouter)
    # We patch the module-level import within solver_router, OR globally.
    # SolverRouter uses `from orion.storage.db import async_session_factory`

    # Create Tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 3. Seed Data
    # Solver 1: Requires "trend_up" regime (will fail matching)
    solver1 = Solver(
        solver_id="s1",
        family_name="StrategyFamilyA",
        config={
            "version_id": "s1",
            "solver_id": "s1",
            "universe": {"required_regime": "trend_up"},
            "features": {"feature_set_id": "v1_legacy"},
            "entry_logic": {},
            "exit_logic": {},
        },
        is_active=True,
        stage="live",
    )
    # Be sure to seed Metrics if needed, although simple retrieval might not need it,
    # checking logic suggests it might fetch metrics. Seeding just in case.
    metrics1 = SolverMetrics(id="m1", solver_id="s1", sharpe_ratio=1.0, info_ratio=1.0)

    # Solver 2: Baseline (will be used as fallback)
    # regime "crash_mode" allows us to verify it *could* match if we wanted,
    # but the point is we force a fallback logic path if S1 is filtered out.
    # Actually, if we want S1 to be filtered out by "trend_up" vs "crash_mode",
    # and S2 to be the baseline fallback, S2 should probably exist.
    solver_baseline = Solver(
        solver_id="baseline_v1",
        family_name="BaselineFamily",
        config={
            "version_id": "baseline_v1",
            "solver_id": "baseline_v1",
            # Baseline usually permissive or matches current regime
            "universe": {"required_regime": "crash_mode"},
            "features": {"feature_set_id": "v1_legacy"},
            "entry_logic": {},
            "exit_logic": {},
        },
        is_active=True,
        stage="live",
    )
    metrics2 = SolverMetrics(id="m2", solver_id="baseline_v1", sharpe_ratio=0.5, info_ratio=0.5)

    async with test_session_factory() as session:
        session.add_all([solver1, metrics1, solver_baseline, metrics2])
        await session.commit()

    # 4. Run Test
    # Patch session factory inside solver_router
    with (
        patch("orion.core.solver_router.async_session_factory", test_session_factory),
        patch("orion.config.system_settings") as mock_settings,
        patch("orion.core.solver_schema.FeatureRegistry.validate_id", return_value=True),
    ):
        # Configure Baseline Setting
        mock_settings.baseline_solver_id = "baseline_v1"

        # Setup Context: "crash_mode" (Should mismatch S1's "trend_up")
        context = LiveContext(
            ticker="SPY",
            regime="crash_mode",
            time_of_day_utc=datetime.datetime.now(datetime.timezone.utc),
            current_stage="live",
        )

        router = SolverRouter()

        # Execute
        selected = await router.select_solvers(context)

    # 5. Verify
    # S1 should be filtered out (regime mismatch).
    # Baseline (S2) should be selected via fallback logic.
    assert len(selected) == 1
    assert selected[0].config.version_id == "baseline_v1"
