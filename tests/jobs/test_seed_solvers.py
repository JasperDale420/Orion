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
async def test_ensure_active_solvers_ready_upserts_on_existing_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upgrade path: seeds must apply even when active solvers already exist —
    only-seed-when-empty left new bucket solvers unroutable after a deploy."""
    from orion.jobs.seed_solvers import ensure_active_solvers_ready

    monkeypatch.setattr(system_settings, "baseline_solver_id", None)

    # Pre-existing DB state: one old-style active solver, no bucket solvers.
    async with async_session_factory() as session:
        session.add(
            Solver(
                solver_id="legacy_solver_v0",
                family_name="Legacy",
                name="Legacy",
                stage="paper",
                status="active",
                is_active=True,
                config={},
                definition_json={},
            )
        )
        await session.commit()

    status = await ensure_active_solvers_ready(stage="paper")

    async with async_session_factory() as session:
        zero_dte = await session.get(Solver, "zero_dte_paper_v1")
        retired = await session.get(Solver, "bullish_sweep_paper_v1")
        legacy = await session.get(Solver, "legacy_solver_v0")

    assert zero_dte is not None and zero_dte.is_active is True
    assert retired is not None and retired.is_active is False
    # Non-seed rows are untouched.
    assert legacy is not None and legacy.is_active is True
    assert status.active_solver_count > 0


@pytest.mark.asyncio
async def test_ensure_active_solvers_ready_raises_for_live_without_solvers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orion.jobs.seed_solvers import ensure_active_solvers_ready

    monkeypatch.setattr(system_settings, "baseline_solver_id", None)

    with pytest.raises(RuntimeError, match="No active solvers configured"):
        await ensure_active_solvers_ready(stage="limited_live")
