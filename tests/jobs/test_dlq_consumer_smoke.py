"""Smoke tests for orion.jobs.dlq_consumer.DLQConsumer.

Audit #21 coverage. run_once fetches PENDING DeadLetterQueue rows and replays
them, mutating each row's status/retry_count in one db_write transaction. We
use the conftest in-memory SQLite DB (db_query/db_write resolve to the test
engine) and patch the per-item _replay_event to isolate the consumer's
orchestration EFFECT (status transitions), not the full feature pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from orion.jobs.dlq_consumer import DLQConsumer
from orion.storage.db import async_session_factory
from orion.storage.models_dlq import DeadLetterQueue


def _make_dlq_row(idx: int) -> DeadLetterQueue:
    return DeadLetterQueue(
        id=f"dlq_{idx}",
        event_type="UW_FLOW",
        ticker="AAPL",
        event_ts_utc=datetime(2026, 6, 10, 14, 0, tzinfo=UTC),
        payload={"ticker": "AAPL"},
        error_message="original failure",
        status="PENDING",
        retry_count=0,
    )


async def _seed(rows: list[DeadLetterQueue]) -> None:
    async with async_session_factory() as session:
        session.add_all(rows)
        await session.commit()


async def _status_of(dlq_id: str) -> tuple[str, int]:
    async with async_session_factory() as session:
        row = await session.get(DeadLetterQueue, dlq_id)
        return row.status, row.retry_count


@pytest.mark.asyncio
async def test_successful_replay_invokes_replay_once() -> None:
    """Happy path: a PENDING row is fetched, _replay_event is invoked, and the
    status mutation PERSISTS (regression: run_once previously fetched rows in
    one session and mutated the detached instances in another, so rows never
    left PENDING and every batch replayed forever)."""
    await _seed([_make_dlq_row(1)])
    consumer = DLQConsumer()
    consumer._replay_event = AsyncMock(return_value=True)  # type: ignore[method-assign]

    await consumer.run_once()

    assert consumer._replay_event.await_count == 1
    status, retry = await _status_of("dlq_1")
    # Fetch+mutate now share one write session -> the update persists.
    assert status == "REPLAYED"
    assert retry == 0


@pytest.mark.asyncio
async def test_unsuccessful_replay_still_invokes_replay() -> None:
    """Replay returns False -> retry_count is bumped and PERSISTED, the row
    stays PENDING for a future retry."""
    await _seed([_make_dlq_row(2)])
    consumer = DLQConsumer()
    consumer._replay_event = AsyncMock(return_value=False)  # type: ignore[method-assign]

    await consumer.run_once()

    assert consumer._replay_event.await_count == 1
    status, retry = await _status_of("dlq_2")
    assert status == "PENDING"
    assert retry == 1


@pytest.mark.asyncio
async def test_replay_crash_is_caught_per_item_and_loop_continues() -> None:
    """Failure path: this is a resilient per-item batch consumer. A crash in
    _replay_event is caught per task (dlq_consumer.py lines 76-79) so the loop
    continues to the next item rather than aborting the batch. We prove the loop
    kept going by asserting _replay_event was awaited for BOTH items even though
    the first raised."""
    await _seed([_make_dlq_row(3), _make_dlq_row(4)])
    consumer = DLQConsumer()
    # First item crashes, second succeeds -> proves the loop kept going.
    consumer._replay_event = AsyncMock(  # type: ignore[method-assign]
        side_effect=[RuntimeError("boom"), True]
    )

    # run_once must not propagate the per-item crash.
    await consumer.run_once()

    assert consumer._replay_event.await_count == 2
    # The crashed item stays PENDING with its retry bumped; the successful
    # second item is marked REPLAYED — both persisted.
    status3, retry3 = await _status_of("dlq_3")
    assert status3 == "PENDING"
    assert retry3 == 1
    assert (await _status_of("dlq_4"))[0] == "REPLAYED"


@pytest.mark.asyncio
async def test_empty_dlq_is_a_noop() -> None:
    """No PENDING rows -> run_once returns without invoking replay."""
    consumer = DLQConsumer()
    consumer._replay_event = AsyncMock(return_value=True)  # type: ignore[method-assign]

    await consumer.run_once()

    assert consumer._replay_event.await_count == 0
