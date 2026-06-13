"""Smoke tests for orion.jobs.solver_promoter.

Audit #21 coverage. The job reads PENDING PromotionRecommendation rows, runs
gate checks against the latest SolverMetrics, and mutates row/solver state in
the DB. These tests use the conftest in-memory SQLite DB (the job calls
``async_session_factory`` directly, which resolves to the test engine) and
assert the persisted EFFECT, not just that the call returned.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from orion.jobs.solver_promoter import run_solver_promotions
from orion.storage.db import async_session_factory
from orion.storage.models_solvers import PromotionRecommendation, Solver, SolverMetrics


def _make_solver(stage: str = "shadow") -> Solver:
    sid = f"solver_{uuid.uuid4().hex[:8]}"
    return Solver(
        solver_id=sid,
        family_name="TestFamily",
        config={"k": "v"},
        is_active=True,
        stage=stage,
    )


def _make_metrics(solver_id: str, *, num_trades: int, sharpe: float, max_dd: float) -> SolverMetrics:
    return SolverMetrics(
        id=str(uuid.uuid4()),
        solver_id=solver_id,
        num_trades=num_trades,
        sharpe_ratio=sharpe,
        max_dd_pct=max_dd,
        evaluated_at_utc=datetime.now(UTC),
    )


def _make_rec(
    solver_id: str, *, current: str, target: str, metrics_snapshot: dict | None = None
) -> PromotionRecommendation:
    return PromotionRecommendation(
        id=str(uuid.uuid4()),
        solver_id=solver_id,
        current_stage=current,
        recommended_stage=target,
        reason="auto test",
        status="PENDING",
        metrics_snapshot=metrics_snapshot,
    )


@pytest.mark.asyncio
async def test_passing_gates_promotes_solver_and_approves_rec() -> None:
    """Happy path: a PENDING rec whose solver passes all gates is APPROVED and
    the solver's stage advances to the recommended stage."""
    solver = _make_solver(stage="shadow")
    metrics = _make_metrics(solver.solver_id, num_trades=20, sharpe=1.5, max_dd=5.0)
    rec = _make_rec(solver.solver_id, current="shadow", target="paper")

    async with async_session_factory() as session:
        session.add_all([solver, metrics, rec])
        await session.commit()
        rec_id = rec.id
        solver_id = solver.solver_id

    summary = await run_solver_promotions()

    assert summary["promoted"] == 1
    assert summary["rejected"] == 0
    assert summary["manual_required"] == 0

    async with async_session_factory() as session:
        refreshed_rec = await session.get(PromotionRecommendation, rec_id)
        refreshed_solver = await session.get(Solver, solver_id)
        assert refreshed_rec.status == "APPROVED"
        assert refreshed_rec.reviewed_by == "auto_promoter"
        assert refreshed_solver.stage == "paper"


@pytest.mark.asyncio
async def test_live_target_requires_manual_and_leaves_rec_pending() -> None:
    """Safety gate: a target stage outside AUTO_PROMOTABLE_STAGES is counted as
    manual_required and the rec is NOT auto-mutated (stays PENDING)."""
    solver = _make_solver(stage="paper")
    metrics = _make_metrics(solver.solver_id, num_trades=50, sharpe=3.0, max_dd=2.0)
    rec = _make_rec(solver.solver_id, current="paper", target="limited_live")

    async with async_session_factory() as session:
        session.add_all([solver, metrics, rec])
        await session.commit()
        rec_id = rec.id
        solver_id = solver.solver_id

    summary = await run_solver_promotions()

    assert summary["manual_required"] == 1
    assert summary["promoted"] == 0

    async with async_session_factory() as session:
        refreshed_rec = await session.get(PromotionRecommendation, rec_id)
        refreshed_solver = await session.get(Solver, solver_id)
        # Left for manual approval: untouched.
        assert refreshed_rec.status == "PENDING"
        assert refreshed_solver.stage == "paper"


@pytest.mark.asyncio
async def test_failing_gate_rejects_rec_without_promoting() -> None:
    """A solver below MIN_TRADES is REJECTED and its stage is unchanged."""
    solver = _make_solver(stage="shadow")
    metrics = _make_metrics(solver.solver_id, num_trades=3, sharpe=1.0, max_dd=5.0)
    rec = _make_rec(solver.solver_id, current="shadow", target="paper")

    async with async_session_factory() as session:
        session.add_all([solver, metrics, rec])
        await session.commit()
        rec_id = rec.id
        solver_id = solver.solver_id

    summary = await run_solver_promotions()

    assert summary["rejected"] == 1
    assert summary["promoted"] == 0

    async with async_session_factory() as session:
        refreshed_rec = await session.get(PromotionRecommendation, rec_id)
        refreshed_solver = await session.get(Solver, solver_id)
        assert refreshed_rec.status == "REJECTED"
        assert refreshed_solver.stage == "shadow"


@pytest.mark.asyncio
async def test_eod_sourced_rec_stays_pending_not_auto_promoted() -> None:
    """Finding 4: an EOD-mutation-sourced PENDING rec (research->paper) must NOT
    be auto-promoted by the Friday job — it stays PENDING for manual approval and
    the solver's stage is untouched, even though it passes all gates."""
    from orion.agents.solver_mutation_processor import EOD_MUTATION_SOURCE

    solver = _make_solver(stage="research")
    metrics = _make_metrics(solver.solver_id, num_trades=30, sharpe=2.0, max_dd=3.0)
    rec = _make_rec(
        solver.solver_id,
        current="research",
        target="paper",
        metrics_snapshot={"source": EOD_MUTATION_SOURCE, "composite_score": 5.0},
    )

    async with async_session_factory() as session:
        session.add_all([solver, metrics, rec])
        await session.commit()
        rec_id = rec.id
        solver_id = solver.solver_id

    summary = await run_solver_promotions()

    assert summary["promoted"] == 0
    assert summary["rejected"] == 0
    assert summary["manual_required"] == 1

    async with async_session_factory() as session:
        refreshed_rec = await session.get(PromotionRecommendation, rec_id)
        refreshed_solver = await session.get(Solver, solver_id)
        # Left PENDING (NOT rejected) and stage untouched.
        assert refreshed_rec.status == "PENDING"
        assert refreshed_solver.stage == "research"


@pytest.mark.asyncio
async def test_normal_gatekeeper_rec_still_auto_promotes_alongside_eod() -> None:
    """A normal (non-EOD-sourced) PENDING rec still auto-promotes even when an
    EOD-sourced rec is present in the same run — the skip is targeted."""
    from orion.agents.solver_mutation_processor import EOD_MUTATION_SOURCE

    normal_solver = _make_solver(stage="shadow")
    normal_metrics = _make_metrics(normal_solver.solver_id, num_trades=20, sharpe=1.5, max_dd=5.0)
    normal_rec = _make_rec(
        normal_solver.solver_id, current="shadow", target="paper", metrics_snapshot={"source": "gatekeeper"}
    )

    eod_solver = _make_solver(stage="research")
    eod_metrics = _make_metrics(eod_solver.solver_id, num_trades=30, sharpe=2.0, max_dd=3.0)
    eod_rec = _make_rec(
        eod_solver.solver_id,
        current="research",
        target="paper",
        metrics_snapshot={"source": EOD_MUTATION_SOURCE},
    )

    async with async_session_factory() as session:
        session.add_all([normal_solver, normal_metrics, normal_rec, eod_solver, eod_metrics, eod_rec])
        await session.commit()
        normal_id = normal_solver.solver_id
        eod_id = eod_solver.solver_id

    summary = await run_solver_promotions()

    assert summary["promoted"] == 1
    assert summary["manual_required"] == 1

    async with async_session_factory() as session:
        assert (await session.get(Solver, normal_id)).stage == "paper"
        assert (await session.get(Solver, eod_id)).stage == "research"


@pytest.mark.asyncio
async def test_no_pending_recs_returns_empty_summary() -> None:
    """Empty queue returns zeroed counts without touching the DB."""
    summary = await run_solver_promotions()
    assert summary == {"promoted": 0, "rejected": 0, "manual_required": 0, "details": []}


@pytest.mark.asyncio
async def test_db_failure_propagates_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure path: this is a single-shot DB transaction job, NOT a resilient
    per-item batch consumer. solver_promoter.py lines 188-190 catch, log via
    ``logger.error(..., exc_info=True)``, and re-raise. Assert the exception
    propagates (fail-fast) rather than being swallowed."""
    import orion.jobs.solver_promoter as sp

    class _BoomFactory:
        def __call__(self):  # noqa: D401 - mimic async_session_factory()
            raise RuntimeError("db down")

    monkeypatch.setattr(sp, "async_session_factory", _BoomFactory())

    with pytest.raises(RuntimeError, match="db down"):
        await run_solver_promotions()
