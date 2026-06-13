"""RC.1: run_review is idempotent per trading date.

The native ingestion trigger (01:05 UTC) is the canonical EOD path. Any second
scheduler — a manual run, a future daemon — that targets the same trading date
must be short-circuited unless ``force=True``. The cursor row is written only
after a successful run, so a crashed run can still be retried.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select

from orion.agents.eod_review_agent import EODReviewAgent
from orion.agents.proposal_builder import ProposalBuilder
from orion.storage import db
from orion.storage.models import JobCursorState


def _agent(tmp_path) -> EODReviewAgent:
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = '{"analysis": "x", "proposals": []}'
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
    return EODReviewAgent(
        llm_client=mock_client,
        vector_store=MagicMock(search=AsyncMock(return_value=[])),
        proposal_builder=ProposalBuilder(output_dir=str(tmp_path / "proposals")),
    )


async def test_second_run_same_date_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("ORION_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    agent = _agent(tmp_path)
    target = datetime.now(UTC).date()

    first = await agent.run_review(target)
    assert not first.get("skipped")
    assert "run_id" in first

    second = await agent.run_review(target)
    assert second["skipped"] is True
    assert second["reason"] == "duplicate_run"


async def test_force_overrides_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("ORION_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    agent = _agent(tmp_path)
    target = datetime.now(UTC).date()

    await agent.run_review(target)
    forced = await agent.run_review(target, force=True)
    assert not forced.get("skipped")
    assert "run_id" in forced


async def test_different_date_not_blocked(tmp_path, monkeypatch):
    from datetime import date

    monkeypatch.setenv("ORION_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    agent = _agent(tmp_path)

    await agent.run_review(date(2026, 6, 10))
    other = await agent.run_review(date(2026, 6, 11))
    assert not other.get("skipped")


async def _cursor_state(agent: EODReviewAgent, target) -> str | None:
    key = agent._cursor_key(target)
    async with db.async_session_factory() as session:
        row = await session.get(JobCursorState, key)
        return None if row is None else agent._decode_state(row.last_seen_id)


async def test_concurrent_claims_same_date_only_one_wins(tmp_path):
    """Finding 2: two near-simultaneous claims on the same date must NOT both
    win — the atomic insert-or-conflict guarantees exactly one ``(True, ...)``
    and one skip. (A check-then-act guard would let both through.)

    Uses a FILE-backed SQLite (real connection pool) rather than the conftest
    in-memory StaticPool, which shares a single connection and so cannot
    faithfully simulate two connections racing on a primary key.
    """
    from datetime import date

    db_path = tmp_path / "race.db"
    file_engine = None
    try:
        db.configure_db(f"sqlite+aiosqlite:///{db_path}")
        file_engine = db.engine
        await db.init_db()
        agent = EODReviewAgent(llm_client=MagicMock())
        target = date(2026, 6, 12)

        results = await asyncio.gather(
            agent._claim_review(target, "run-A", force=False),
            agent._claim_review(target, "run-B", force=False),
        )

        won = [r for r in results if r[0]]
        lost = [r for r in results if not r[0]]
        assert len(won) == 1, results
        assert len(lost) == 1, results
        assert lost[0][1] == "in_progress"

        # Exactly one RUNNING claim row exists.
        async with db.async_session_factory() as session:
            rows = (await session.execute(select(JobCursorState))).scalars().all()
            assert len(rows) == 1
            assert agent._decode_state(rows[0].last_seen_id) == "RUNNING"
    finally:
        # Dispose the file engine and rebind the conftest in-memory DB so the
        # autouse teardown drops tables on a live engine (not the disposed one).
        if file_engine is not None:
            await file_engine.dispose()
        db.configure_db("sqlite+aiosqlite:///:memory:")
        await db.init_db()


async def test_second_run_same_date_skipped_via_atomic_claim(tmp_path, monkeypatch):
    """End-to-end: a completed run records SUCCESS; a second same-date run is
    skipped as a duplicate (atomic-claim path, conftest in-memory DB)."""
    monkeypatch.setenv("ORION_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    agent = _agent(tmp_path)
    target = datetime.now(UTC).date()

    first = await agent.run_review(target)
    assert not first.get("skipped")
    assert await _cursor_state(agent, target) == "SUCCESS"

    second = await agent.run_review(target)
    assert second["skipped"] is True
    assert second["reason"] == "duplicate_run"


async def test_failed_run_allows_retry(tmp_path, monkeypatch):
    """A FAILED claim must NOT block a later run — it is treated like absent."""
    monkeypatch.setenv("ORION_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    agent = _agent(tmp_path)
    target = datetime.now(UTC).date()

    # Force the body to blow up so the claim is marked FAILED and re-raised.
    monkeypatch.setattr(agent, "_perform_review", AsyncMock(side_effect=RuntimeError("boom")))
    try:
        await agent.run_review(target)
    except RuntimeError:
        pass
    assert await _cursor_state(agent, target) == "FAILED"

    # A healthy retry must proceed (FAILED treated as absent) and end SUCCESS.
    monkeypatch.undo()
    monkeypatch.setenv("ORION_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    retry = await agent.run_review(target)
    assert not retry.get("skipped")
    assert await _cursor_state(agent, target) == "SUCCESS"


async def test_stale_running_claim_is_taken_over(tmp_path, monkeypatch):
    """A RUNNING claim older than the stale TTL (crashed run) is taken over."""
    monkeypatch.setenv("ORION_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    agent = _agent(tmp_path)
    target = datetime.now(UTC).date()
    key = agent._cursor_key(target)

    # Plant a stale RUNNING claim (7h old — past the 6h TTL).
    async with db.async_session_factory() as session:
        session.add(
            JobCursorState(
                key=key,
                last_seen_ts_utc=datetime.now(UTC) - timedelta(hours=7),
                last_seen_id=agent._encode_state("RUNNING", "dead-run-id"),
            )
        )
        await session.commit()

    result = await agent.run_review(target)
    assert not result.get("skipped")
    assert await _cursor_state(agent, target) == "SUCCESS"


async def test_fresh_running_claim_blocks(tmp_path, monkeypatch):
    """A fresh (non-stale) RUNNING claim short-circuits a second run as
    'in_progress' — it does NOT take over."""
    monkeypatch.setenv("ORION_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    agent = _agent(tmp_path)
    target = datetime.now(UTC).date()
    key = agent._cursor_key(target)

    async with db.async_session_factory() as session:
        session.add(
            JobCursorState(
                key=key,
                last_seen_ts_utc=datetime.now(UTC),
                last_seen_id=agent._encode_state("RUNNING", "live-run-id"),
            )
        )
        await session.commit()

    result = await agent.run_review(target)
    assert result["skipped"] is True
    assert result["reason"] == "in_progress"


async def test_force_rebypasses_success_via_atomic_claim(tmp_path, monkeypatch):
    """force=True bypasses the SUCCESS skip but still uses the atomic claim
    (re-claims the existing SUCCESS row and re-runs)."""
    monkeypatch.setenv("ORION_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    agent = _agent(tmp_path)
    target = datetime.now(UTC).date()

    await agent.run_review(target)
    assert await _cursor_state(agent, target) == "SUCCESS"

    forced = await agent.run_review(target, force=True)
    assert not forced.get("skipped")
    assert "run_id" in forced
    assert await _cursor_state(agent, target) == "SUCCESS"


# ── Round-2 finding: reclaim CAS + finalize hardening ─────────────────────────


async def _run_concurrent_reclaim_race(tmp_path, *, planter, force=False):
    """Plant a reclaimable row, then fire two concurrent _claim_review calls on
    a FILE-backed SQLite (real pool) and return the (results, agent, target).

    Exactly one must win via the compare-and-swap; the other must lose the race
    (claimed=False) — never two winners.
    """
    from datetime import date

    db_path = tmp_path / "reclaim_race.db"
    file_engine = None
    try:
        db.configure_db(f"sqlite+aiosqlite:///{db_path}")
        file_engine = db.engine
        await db.init_db()
        agent = EODReviewAgent(llm_client=MagicMock())
        target = date(2026, 6, 12)
        key = agent._cursor_key(target)

        async with db.async_session_factory() as session:
            session.add(planter(agent, key))
            await session.commit()

        results = await asyncio.gather(
            agent._claim_review(target, "run-A", force=force),
            agent._claim_review(target, "run-B", force=force),
        )
        return results, agent, target
    finally:
        if file_engine is not None:
            await file_engine.dispose()
        db.configure_db("sqlite+aiosqlite:///:memory:")
        await db.init_db()


# The loser of a concurrent reclaim is rejected by ONE of two no-double-claim
# paths, depending on commit interleaving:
#  - both read the OLD reclaimable value, both issue the CAS → one matches
#    (rowcount 1), the other matches 0 rows → 'lost_claim_race';
#  - the winner commits BEFORE the loser reads → the loser reads the winner's
#    fresh RUNNING row and short-circuits as 'in_progress'.
# Both prove the invariant that matters: exactly one winner, never two.
_NO_DOUBLE_CLAIM_REASONS = {"lost_claim_race", "in_progress"}


async def test_concurrent_failed_reclaim_only_one_wins(tmp_path):
    """Both callers race to reclaim the SAME FAILED row; the compare-and-swap
    guarantees exactly one winner. A read-modify-write reclaim would let BOTH
    proceed."""

    def planter(agent, key):
        return JobCursorState(
            key=key,
            last_seen_ts_utc=datetime.now(UTC),
            last_seen_id=agent._encode_state("FAILED", "dead-run"),
        )

    results, agent, target = await _run_concurrent_reclaim_race(tmp_path, planter=planter)

    won = [r for r in results if r[0]]
    lost = [r for r in results if not r[0]]
    assert len(won) == 1, results
    assert len(lost) == 1, results
    assert lost[0][1] in _NO_DOUBLE_CLAIM_REASONS, results


async def test_concurrent_stale_running_takeover_only_one_wins(tmp_path):
    """Two callers race to take over the SAME stale RUNNING row; the CAS lets
    exactly one win, never two."""

    def planter(agent, key):
        return JobCursorState(
            key=key,
            last_seen_ts_utc=datetime.now(UTC) - timedelta(hours=7),
            last_seen_id=agent._encode_state("RUNNING", "crashed-run"),
        )

    results, agent, target = await _run_concurrent_reclaim_race(tmp_path, planter=planter)

    won = [r for r in results if r[0]]
    lost = [r for r in results if not r[0]]
    assert len(won) == 1, results
    assert len(lost) == 1, results
    assert lost[0][1] in _NO_DOUBLE_CLAIM_REASONS, results


async def test_force_reclaims_fresh_running(tmp_path, monkeypatch):
    """force=True must reclaim even a FRESH (non-stale) RUNNING row — the
    operator override for a wedged claim. Without force this would skip as
    'in_progress'."""
    monkeypatch.setenv("ORION_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    agent = _agent(tmp_path)
    target = datetime.now(UTC).date()
    key = agent._cursor_key(target)

    async with db.async_session_factory() as session:
        session.add(
            JobCursorState(
                key=key,
                last_seen_ts_utc=datetime.now(UTC),  # fresh, not stale
                last_seen_id=agent._encode_state("RUNNING", "wedged-run"),
            )
        )
        await session.commit()

    # Sanity: without force this would block.
    claimed, reason = await agent._claim_review(target, "no-force", force=False)
    assert claimed is False
    assert reason == "in_progress"

    # With force it reclaims the fresh RUNNING row and runs to SUCCESS.
    forced = await agent.run_review(target, force=True)
    assert not forced.get("skipped")
    assert await _cursor_state(agent, target) == "SUCCESS"


async def test_lost_claim_race_run_review_returns_clean_skip(tmp_path, monkeypatch):
    """A caller that LOSES the CAS race must surface as a clean skip dict from
    run_review (skipped=True, reason='lost_claim_race') — NOT an exception, and
    the review body must NOT run."""
    monkeypatch.setenv("ORION_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    agent = _agent(tmp_path)
    target = datetime.now(UTC).date()

    perform = AsyncMock()
    monkeypatch.setattr(agent, "_perform_review", perform)
    monkeypatch.setattr(agent, "_claim_review", AsyncMock(return_value=(False, "lost_claim_race")))

    result = await agent.run_review(target)
    assert result["skipped"] is True
    assert result["reason"] == "lost_claim_race"
    perform.assert_not_called()


async def test_claim_db_error_propagates_no_fail_open(tmp_path, monkeypatch):
    """If the claim machinery itself errors, run_review must RAISE (not fail
    open and proceed into the review body). A degraded guard letting two runs
    proceed is the worse failure."""
    monkeypatch.setenv("ORION_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    agent = _agent(tmp_path)
    target = datetime.now(UTC).date()

    # Make the claim raise; assert run_review propagates and never enters body.
    perform = AsyncMock()
    monkeypatch.setattr(agent, "_perform_review", perform)
    monkeypatch.setattr(agent, "_claim_review", AsyncMock(side_effect=RuntimeError("db down")))

    raised = False
    try:
        await agent.run_review(target)
    except RuntimeError:
        raised = True
    assert raised, "claim DB error must propagate, not fail open"
    perform.assert_not_called()


async def test_finish_review_retries_once_then_logs_error(tmp_path, monkeypatch):
    """_finish_review retries the terminal-state write once on exception; if it
    still fails it logs at ERROR (the wedge that force=True recovers)."""
    monkeypatch.setenv("ORION_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    agent = _agent(tmp_path)
    target = datetime.now(UTC).date()

    import orion.agents.eod_review_agent as mod

    calls = {"write": 0}

    async def failing_write(fn):
        calls["write"] += 1
        raise RuntimeError("write failed")

    monkeypatch.setattr(mod, "db_write", failing_write)
    monkeypatch.setattr(agent, "_FINISH_RETRY_DELAY_SECONDS", 0.0)

    error_logs: list[str] = []
    monkeypatch.setattr(mod.logger, "error", lambda event, **kw: error_logs.append(event))

    # Must not raise (best-effort finalize), but must retry once (2 writes) and
    # log the loud ERROR.
    await agent._finish_review(target, "run-x", "FAILED")
    assert calls["write"] == 2, "finalize must retry exactly once"
    assert "eod_review_cursor_finalize_failed" in error_logs


async def test_finish_review_retry_succeeds_second_attempt(tmp_path, monkeypatch):
    """If the first finalize write fails but the retry succeeds, no ERROR is
    logged and the terminal state is persisted."""
    monkeypatch.setenv("ORION_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    agent = _agent(tmp_path)
    target = datetime.now(UTC).date()

    import orion.agents.eod_review_agent as mod

    # Seed a RUNNING claim that this run owns, so the retry actually finalizes it.
    key = agent._cursor_key(target)
    run_id = "owner-run"
    async with db.async_session_factory() as session:
        session.add(
            JobCursorState(
                key=key,
                last_seen_ts_utc=datetime.now(UTC),
                last_seen_id=agent._encode_state("RUNNING", run_id),
            )
        )
        await session.commit()

    real_write = mod.db_write
    calls = {"n": 0}

    async def flaky_write(fn):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return await real_write(fn)

    monkeypatch.setattr(mod, "db_write", flaky_write)
    monkeypatch.setattr(agent, "_FINISH_RETRY_DELAY_SECONDS", 0.0)
    error_logs: list[str] = []
    monkeypatch.setattr(mod.logger, "error", lambda event, **kw: error_logs.append(event))

    await agent._finish_review(target, run_id, "SUCCESS")
    assert calls["n"] == 2
    assert error_logs == []
    assert await _cursor_state(agent, target) == "SUCCESS"


async def test_finalize_after_takeover_does_not_clobber_new_claim(tmp_path):
    """Round-3 CAS-finalization: run A holds RUNNING:A, a force/stale takeover
    run B CAS-reclaims to RUNNING:B, and THEN A finalizes SUCCESS. A's terminal
    write must be a CAS on its own RUNNING claim — rowcount 0 → leave B's claim
    untouched (a PK-keyed write here would mark the date SUCCESS while B is
    still running, making future same-date runs skip incorrectly)."""
    from datetime import date

    agent = _agent(tmp_path)
    target = date(2026, 6, 12)
    key = agent._cursor_key(target)

    # Run A claims (fresh INSERT path).
    claimed, _ = await agent._claim_review(target, "run-A", force=False)
    assert claimed

    # Run B force-takes-over A's fresh RUNNING claim via the CAS.
    claimed_b, _ = await agent._claim_review(target, "run-B", force=True)
    assert claimed_b

    # A (still alive, unaware) tries to finalize SUCCESS — must NOT land.
    await agent._finish_review(target, "run-A", "SUCCESS")
    async with db.async_session_factory() as session:
        row = await session.get(JobCursorState, key)
        assert row is not None
        assert row.last_seen_id == agent._encode_state("RUNNING", "run-B"), (
            "run A's stale finalize clobbered run B's takeover claim"
        )

    # B's own finalize DOES land (it owns the claim).
    await agent._finish_review(target, "run-B", "FAILED")
    assert await _cursor_state(agent, target) == "FAILED"
