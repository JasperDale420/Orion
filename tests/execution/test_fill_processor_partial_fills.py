import os

# Force SQLite for unit tests
os.environ["DB_URL"] = "sqlite+aiosqlite:///:memory:"

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from orion.execution.fill_processor import FillProcessor
from orion.storage.db import async_session_factory, init_db
from orion.storage.models_execution import FillRecord
from orion.storage.models_risk import ProcessedFill


@pytest.mark.asyncio
async def test_partial_fills_are_processed_incrementally(risk_manager_factory) -> None:
    await init_db()
    processor = FillProcessor()
    risk_manager = risk_manager_factory()
    risk_manager.current_equity = 10000.0
    remove_pending_fn = AsyncMock()

    fill_one = {
        "id": "broker-1",
        "client_order_id": "orion_123",
        "symbol": "TEST",
        "filled_qty": 4.0,
        "qty": 10.0,
        "filled_avg_price": 2.5,
        "side": "buy",
    }
    fill_two = {
        "id": "broker-1",
        "client_order_id": "orion_123",
        "symbol": "TEST",
        "filled_qty": 10.0,
        "qty": 10.0,
        "filled_avg_price": 2.5,
        "side": "buy",
    }

    await processor.process_single_fill(fill_one, risk_manager, remove_pending_fn)
    assert risk_manager.positions["TEST"]["qty"] == pytest.approx(4.0)

    await processor.process_single_fill(fill_two, risk_manager, remove_pending_fn)
    assert risk_manager.positions["TEST"]["qty"] == pytest.approx(10.0)

    assert remove_pending_fn.await_count == 1
    assert processor._partial_fill_tracker == {}

    async with async_session_factory() as session:
        markers = (await session.execute(select(ProcessedFill.fill_id))).scalars().all()
        assert set(markers) == {"broker-1:4.0", "broker-1:10.0"}

        fill_row = (
            (await session.execute(select(FillRecord).where(FillRecord.broker_order_id == "broker-1")))
            .scalars()
            .first()
        )
        assert fill_row is not None
        assert fill_row.filled_qty == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_atomic_write_failure_leaves_nothing_committed_and_retry_succeeds(risk_manager_factory) -> None:
    """The fill row, the risk-state update, and the processed-fill marker land
    in ONE transaction. If any part of it fails, NOTHING commits -- no fill
    row, no marker, no risk-state change, and the in-memory tracker never
    advances -- so a subsequent poll with the same cumulative fill data
    retries cleanly and lands exactly once.

    This is the guarantee the atomic rewrite provides that the earlier
    compensating-delete design could not: there is no window where a durable
    marker exists without the risk update it's supposed to attest to, even
    across a hard process crash or a failed compensating write (2026-08-18/19
    RCA).
    """
    await init_db()
    processor = FillProcessor()
    risk_manager = risk_manager_factory()
    risk_manager.current_equity = 10000.0
    remove_pending_fn = AsyncMock()

    fill = {
        "id": "broker-atomic-1",
        "client_order_id": "orion_atomic",
        "symbol": "ATOM",
        "filled_qty": 5.0,
        "qty": 5.0,
        "filled_avg_price": 3.0,
        "side": "buy",
    }

    real_prepare = risk_manager.prepare_fill_for_session
    risk_manager.prepare_fill_for_session = AsyncMock(side_effect=RuntimeError("risk save failed"))

    await processor.process_single_fill(fill, risk_manager, remove_pending_fn)

    assert processor._partial_fill_tracker.get("broker-atomic-1", 0.0) == 0.0
    assert "ATOM" not in risk_manager.positions

    async with async_session_factory() as session:
        marker = (
            (await session.execute(select(ProcessedFill).where(ProcessedFill.fill_id == "broker-atomic-1:5.0")))
            .scalars()
            .first()
        )
        assert marker is None
        fill_row = (
            (await session.execute(select(FillRecord).where(FillRecord.broker_order_id == "broker-atomic-1")))
            .scalars()
            .first()
        )
        assert fill_row is None

    risk_manager.prepare_fill_for_session = real_prepare

    await processor.process_single_fill(fill, risk_manager, remove_pending_fn)

    assert risk_manager.positions["ATOM"]["qty"] == pytest.approx(5.0)
    async with async_session_factory() as session:
        marker = (
            (await session.execute(select(ProcessedFill).where(ProcessedFill.fill_id == "broker-atomic-1:5.0")))
            .scalars()
            .first()
        )
        assert marker is not None
        fill_row = (
            (await session.execute(select(FillRecord).where(FillRecord.broker_order_id == "broker-atomic-1")))
            .scalars()
            .first()
        )
        assert fill_row is not None


@pytest.mark.asyncio
async def test_commit_ambiguous_failure_still_applies_effect_on_retry(
    risk_manager_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A commit-acknowledgement loss: the atomic transaction actually commits
    on the DB server, but this process never receives confirmation (e.g. the
    connection drops at exactly that moment), so _write_fill_atomically raises
    and @db_retry retries. Without a check, the retry's attempt to insert the
    SAME ProcessedFill row would collide with the one that already landed,
    exhaust all retries, and leave the fill durably marked but never applied
    to live risk state (2026-08-19 RCA). prepare_fill_for_session detects the
    already-durable marker on the retry and returns the effect for the caller
    to apply instead of re-attempting writes that would collide.
    """
    import orion.execution.fill_processor as fp_mod
    from orion.shared.db_utils import db_write as real_db_write

    await init_db()
    processor = FillProcessor()
    risk_manager = risk_manager_factory()
    risk_manager.current_equity = 10000.0
    remove_pending_fn = AsyncMock()

    fill = {
        "id": "broker-ambiguous-1",
        "client_order_id": "orion_ambiguous",
        "symbol": "AMBIG",
        "filled_qty": 4.0,
        "qty": 4.0,
        "filled_avg_price": 2.0,
        "side": "buy",
    }

    call_count = {"n": 0}

    async def flaky_db_write(write_fn):
        call_count["n"] += 1
        result = await real_db_write(write_fn)  # the transaction genuinely commits
        if call_count["n"] == 1:
            raise RuntimeError("commit ack lost")  # ...but this process never learns that
        return result

    monkeypatch.setattr(fp_mod, "db_write", flaky_db_write)
    await processor.process_single_fill(fill, risk_manager, remove_pending_fn)

    assert call_count["n"] == 2  # first attempt raised, @db_retry's second attempt recovered
    assert risk_manager.positions["AMBIG"]["qty"] == pytest.approx(4.0)
    assert processor._partial_fill_tracker == {}

    async with async_session_factory() as session:
        markers = (await session.execute(select(ProcessedFill.fill_id))).scalars().all()
        assert markers.count("broker-ambiguous-1:4.0") == 1  # exactly one row, no collision left behind


@pytest.mark.asyncio
async def test_commit_ambiguous_failure_on_final_retry_still_applies_effect(
    risk_manager_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same commit-acknowledgement loss, but landing on @db_retry's LAST (3rd)
    attempt: no further attempt exists for prepare_fill_for_session's own
    in-session marker check to catch it. _write_fill_atomically's
    post-retry-exhaustion recovery (a fresh, out-of-transaction check) must
    still recognize the durably-committed marker and apply its effect,
    instead of leaving the fill stuck durably marked but never applied to
    live risk state (2026-08-19 RCA, round 2).
    """
    import orion.execution.fill_processor as fp_mod
    from orion.shared.db_utils import db_write as real_db_write

    await init_db()
    processor = FillProcessor()
    risk_manager = risk_manager_factory()
    risk_manager.current_equity = 10000.0
    remove_pending_fn = AsyncMock()

    fill = {
        "id": "broker-final-ambiguous-1",
        "client_order_id": "orion_final_ambiguous",
        "symbol": "FINAMB",
        "filled_qty": 6.0,
        "qty": 6.0,
        "filled_avg_price": 1.5,
        "side": "buy",
    }

    call_count = {"n": 0}

    async def flaky_db_write(write_fn):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise RuntimeError(f"transient db error, attempt {call_count['n']}")  # never commits
        result = await real_db_write(write_fn)  # the 3rd (last) attempt genuinely commits...
        raise RuntimeError("commit ack lost on final attempt")  # ...but this process never learns that

    monkeypatch.setattr(fp_mod, "db_write", flaky_db_write)
    await processor.process_single_fill(fill, risk_manager, remove_pending_fn)

    assert call_count["n"] == 3
    assert risk_manager.positions["FINAMB"]["qty"] == pytest.approx(6.0)
    assert processor._partial_fill_tracker == {}

    async with async_session_factory() as session:
        markers = (await session.execute(select(ProcessedFill.fill_id))).scalars().all()
        assert markers.count("broker-final-ambiguous-1:6.0") == 1


@pytest.mark.asyncio
async def test_restart_with_processed_marker_present_still_cleans_up_pending_order(risk_manager_factory) -> None:
    """A fresh FillProcessor + fresh RiskManager (simulating a restart) must
    not re-drive risk-state application for a fill whose durable
    processed-marker already exists from a prior process's lifetime -- but
    IF that prior attempt crashed after the atomic fill-write committed and
    before it reached the pending-order cleanup (the steps after
    _write_fill_atomically in process_single_fill), this early-return path
    is the ONLY remaining chance to clean up the now-orphaned pending-order
    tracking row, since every later poll of the same fill short-circuits
    here. remove_pending_order is itself idempotent (no-ops if the row is
    already gone) and swallows its own persistence failures rather than
    raising, so it is always safe to call again on this path once the fill
    is known to be complete (2026-08-19 RCA, codex review).
    """
    await init_db()

    async with async_session_factory() as session:
        session.add(
            ProcessedFill(fill_id="broker-restart-1:7.0", client_order_id="orion_restart", ticker="RST", qty=7.0)
        )
        await session.commit()

    processor = FillProcessor()
    risk_manager = risk_manager_factory()
    remove_pending_fn = AsyncMock()

    fill = {
        "id": "broker-restart-1",
        "client_order_id": "orion_restart",
        "symbol": "RST",
        "filled_qty": 7.0,
        "qty": 7.0,
        "filled_avg_price": 1.5,
        "side": "sell",
    }

    await processor.process_single_fill(fill, risk_manager, remove_pending_fn)

    assert "RST" not in risk_manager.positions  # risk state itself is not redriven
    remove_pending_fn.assert_awaited_once_with("orion_restart")
    assert processor._partial_fill_tracker == {}


@pytest.mark.asyncio
async def test_restart_with_processed_marker_present_for_partial_fill_leaves_pending_order(
    risk_manager_factory,
) -> None:
    """Same restart scenario, but the durably-processed marker is for an
    INTERMEDIATE (not yet complete) cumulative fill amount -- the order is
    still open, so its pending-order tracking row must NOT be removed.
    """
    await init_db()

    async with async_session_factory() as session:
        session.add(
            ProcessedFill(fill_id="broker-restart-2:4.0", client_order_id="orion_restart2", ticker="RST2", qty=4.0)
        )
        await session.commit()

    processor = FillProcessor()
    risk_manager = risk_manager_factory()
    remove_pending_fn = AsyncMock()

    fill = {
        "id": "broker-restart-2",
        "client_order_id": "orion_restart2",
        "symbol": "RST2",
        "filled_qty": 4.0,
        "qty": 10.0,
        "filled_avg_price": 1.5,
        "side": "sell",
    }

    await processor.process_single_fill(fill, risk_manager, remove_pending_fn)

    remove_pending_fn.assert_not_awaited()
