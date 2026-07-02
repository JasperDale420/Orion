import pytest
from sqlalchemy import func, select

from orion.config import system_settings
from orion.storage.db import async_session_factory
from orion.storage.models_solvers import Solver, SolverMetrics


@pytest.mark.asyncio
async def test_seed_default_solvers_populates_empty_db(monkeypatch: pytest.MonkeyPatch) -> None:
    from orion.jobs.seed_solvers import DEFAULT_BASELINE_SOLVER_ID, SEED_SOLVERS, seed_default_solvers

    monkeypatch.setattr(system_settings, "baseline_solver_id", None)

    summary = await seed_default_solvers()

    assert summary["created"] == len(SEED_SOLVERS)
    assert summary["skipped"] == 0
    assert summary["baseline_solver_id"] == DEFAULT_BASELINE_SOLVER_ID

    async with async_session_factory() as session:
        solver_count = await session.scalar(select(func.count()).select_from(Solver))
        metrics_count = await session.scalar(select(func.count()).select_from(SolverMetrics))
        baseline = await session.get(Solver, DEFAULT_BASELINE_SOLVER_ID)

    assert solver_count == len(SEED_SOLVERS)
    assert metrics_count == len(SEED_SOLVERS)
    assert baseline is not None
    assert baseline.is_active is True
    assert baseline.status == "active"
    # Every bucket's rule must be routable: each implemented flow rule appears
    # in at least one seeded solver (the 0DTE/short-swing gap left two buckets
    # unable to trade — every candidate died at "Ensemble Rejected").
    seeded_rules = {rule for s in SEED_SOLVERS for rule in s["config"]["rules"]}
    for rule_id in (
        "rule_bullish_sweep_v1",
        "rule_bearish_put_pressure_v1",
        "rule_0dte_sweep_v1",
        "rule_swing_entry_v1",
        "rule_short_swing_entry_v1",
    ):
        assert rule_id in seeded_rules


@pytest.mark.asyncio
async def test_ensure_active_solvers_ready_seeds_paper_and_sets_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orion.jobs.seed_solvers import DEFAULT_BASELINE_SOLVER_ID, SEED_SOLVERS, ensure_active_solvers_ready

    monkeypatch.setattr(system_settings, "baseline_solver_id", None)

    status = await ensure_active_solvers_ready(stage="paper")

    assert status.seeded is True
    # Retired solvers (absorbed rules) are seeded inactive.
    active_seeds = [s for s in SEED_SOLVERS if s["is_active"]]
    assert status.active_solver_count == len(active_seeds)
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
