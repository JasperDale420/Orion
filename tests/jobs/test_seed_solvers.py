import pytest
from sqlalchemy import func, select

from orion.config import system_settings
from orion.storage.db import async_session_factory
from orion.storage.models_solvers import Solver, SolverMetrics


@pytest.mark.asyncio
async def test_seed_default_solvers_populates_empty_db(monkeypatch: pytest.MonkeyPatch) -> None:
    from orion.jobs.seed_solvers import DEFAULT_BASELINE_SOLVER_ID, seed_default_solvers

    monkeypatch.setattr(system_settings, "baseline_solver_id", None)

    summary = await seed_default_solvers()

    assert summary["created"] == 5
    assert summary["skipped"] == 0
    assert summary["baseline_solver_id"] == DEFAULT_BASELINE_SOLVER_ID

    async with async_session_factory() as session:
        solver_count = await session.scalar(select(func.count()).select_from(Solver))
        metrics_count = await session.scalar(select(func.count()).select_from(SolverMetrics))
        baseline = await session.get(Solver, DEFAULT_BASELINE_SOLVER_ID)

    assert solver_count == 5
    assert metrics_count == 5
    assert baseline is not None
    assert baseline.is_active is True
    assert baseline.status == "active"


@pytest.mark.asyncio
async def test_ensure_active_solvers_ready_seeds_paper_and_sets_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orion.jobs.seed_solvers import DEFAULT_BASELINE_SOLVER_ID, ensure_active_solvers_ready

    monkeypatch.setattr(system_settings, "baseline_solver_id", None)

    status = await ensure_active_solvers_ready(stage="paper")

    assert status.seeded is True
    assert status.active_solver_count == 5
    assert status.baseline_solver_id == DEFAULT_BASELINE_SOLVER_ID
    assert system_settings.baseline_solver_id == DEFAULT_BASELINE_SOLVER_ID


@pytest.mark.asyncio
async def test_ensure_active_solvers_ready_raises_for_live_without_solvers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orion.jobs.seed_solvers import ensure_active_solvers_ready

    monkeypatch.setattr(system_settings, "baseline_solver_id", None)

    with pytest.raises(RuntimeError, match="No active solvers configured"):
        await ensure_active_solvers_ready(stage="limited_live")
