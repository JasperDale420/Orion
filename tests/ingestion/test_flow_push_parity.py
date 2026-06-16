"""Flow-push event-id parity + shadow dedup (hard B1 requirement).

The contract: a pushed flow event and the corresponding Heber-poll flow event
carry the SAME Gateway-minted event_id, so the union the shadow path feeds into
the pipeline persists exactly ONE bronze row — shadow double-delivery can never
manufacture a double candidate.

These tests exercise the real DeduplicationEngine + bronze persistence against
the autouse in-memory SQLite DB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from orion.connectors.gateway_stream_client import GatewayStreamClient
from orion.processing.deduper import DeduplicationEngine
from orion.processing.persistence import persist_bronze_events
from orion.storage.db import async_session_factory
from orion.storage.models import BronzeEvent

pytestmark = pytest.mark.asyncio


def _poll_event(event_id: str) -> BronzeEvent:
    """A poll-path BronzeEvent (the shape _heber_row_to_event yields)."""
    return BronzeEvent(
        event_id=event_id,
        source="UW",
        event_type="UW_FLOW",
        event_ts_utc=datetime(2026, 2, 5, 14, 31, tzinfo=UTC),
        received_ts_utc=datetime.now(UTC),
        session="REGULAR",
        ticker="AAPL",
        payload={"ticker": "AAPL", "put_call": "C", "premium_usd": 125000.0},
    )


async def _push_event(event_id: str) -> BronzeEvent:
    """A push-path BronzeEvent built by the real _process_flow_message."""
    client = GatewayStreamClient(gateway_url="http://localhost:8000", api_key="k")
    msg = {
        "type": "data",
        "feed": "flow_alerts",
        "symbol": "AAPL",
        "event_id": event_id,
        "envelope": {"event_id": event_id, "ts_event": "2026-02-05T14:31:00Z"},
        "data": {
            "ticker": "AAPL",
            "underlying": "AAPL",
            "put_call": "call",
            "premium": 125000.0,
            "timestamp": "2026-02-05T14:31:00Z",
        },
    }
    await client._process_flow_message(msg)
    events = client.drain_flow_events()
    assert len(events) == 1
    ev = events[0]
    ev.session = "REGULAR"
    return ev


def _make_service(window_s: float = 900.0):
    """Build an IngestionService with its heavy constructor deps mocked out.

    Only the rolling shadow-parity state is exercised here.
    """
    from unittest.mock import MagicMock, patch

    with (
        patch("orion.ingestion.service.HealthMonitor"),
        patch("orion.ingestion.service.UniverseManager"),
        patch("orion.ingestion.service.FeatureEngine"),
        patch("orion.ingestion.service.RuleEngine"),
        patch("orion.ingestion.service.xcals"),
        patch("orion.ingestion.service.create_gateway_stream_client", return_value=MagicMock()),
    ):
        from orion.ingestion.service import IngestionService

        svc = IngestionService()
    svc._parity_window_s = window_s
    return svc


class _Clock:
    """Monkeypatchable stand-in for service-module `datetime` (now() only)."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)

    def now(self, tz=None):  # noqa: ANN001 - mirrors datetime.now signature
        return self._now


async def _latest_parity_row():
    from sqlalchemy import desc, select

    from orion.storage.models_flow_parity import FlowPushParity

    async with async_session_factory() as session:
        return (await session.execute(select(FlowPushParity).order_by(desc(FlowPushParity.id)).limit(1))).scalar_one()


async def test_lagged_poll_counts_matched_not_missed(monkeypatch):
    """Push in cycle N, poll in cycle N+1 within the window → matched, not missed.

    This is the O3 fix: a same-cycle intersection would record cycle N's push
    id as missed_by_push the moment the window elapsed. The rolling window must
    instead see poll catch up in cycle N+1 and classify it matched.
    """
    svc = _make_service(window_s=900.0)
    clock = _Clock(datetime(2026, 2, 5, 14, 30, tzinfo=UTC))
    monkeypatch.setattr("orion.ingestion.service.datetime", clock)

    shared_id = "uwblake2b-lag-1"

    # Cycle N: push delivers, poll empty (Silver hasn't landed it yet).
    push = await _push_event(shared_id)
    await svc._record_flow_parity([push], [], "trace-N")
    row_n = await _latest_parity_row()
    assert row_n.push_count == 1
    assert row_n.poll_count == 0
    # Nothing has elapsed past the window, so no miss is finalized yet.
    assert row_n.missed_by_push_count == 0
    assert row_n.window_seconds == 900

    # Cycle N+1, 60s later (well within the 900s window): poll catches up.
    clock.advance(60)
    poll = _poll_event(shared_id)
    await svc._record_flow_parity([], [poll], "trace-N1")
    row_n1 = await _latest_parity_row()
    assert row_n1.matched_count == 1
    assert row_n1.missed_by_push_count == 0


async def test_lagged_match_not_recounted_missed_after_expiry(monkeypatch):
    """Round-4 finding: after a lagged match, advancing past BOTH window
    expirations must not recount the id as missed.

    Before the fix the matched id was left live in the poll map; once push
    expired and then poll expired, ``push_any`` was empty and the same already-
    matched event was wrongly flagged ``missed_by_push``. Finalizing the match
    (removing it from both maps) makes the id unrecountable.
    """
    svc = _make_service(window_s=300.0)
    clock = _Clock(datetime(2026, 2, 5, 14, 30, tzinfo=UTC))
    monkeypatch.setattr("orion.ingestion.service.datetime", clock)

    shared_id = "uwblake2b-lag-expiry-1"

    # Push, then poll 60s later → matched and finalized.
    await svc._record_flow_parity([await _push_event(shared_id)], [], "trace-N")
    clock.advance(60)
    await svc._record_flow_parity([], [_poll_event(shared_id)], "trace-N1")
    assert (await _latest_parity_row()).matched_count == 1
    # The match removed the id from both maps.
    assert shared_id not in svc._push_seen
    assert shared_id not in svc._poll_seen

    # Advance well past the window (poll first-seen + 300s) over several empty
    # cycles — the id must never resurface as a miss.
    for i, step in enumerate((150, 150, 200)):
        clock.advance(step)
        await svc._record_flow_parity([], [], f"trace-expiry-{i}")
        row = await _latest_parity_row()
        assert row.missed_by_push_count == 0, "matched id was recounted as missed after expiry"
        assert row.missed_by_poll_count == 0


async def test_push_arriving_past_window_is_missed_not_matched(monkeypatch):
    """Round-5: a push delivery arriving AFTER the window — in the very cycle
    that would have expired the poll entry — must count missed_by_push, not
    matched, and must not pollute the latency sample with an over-window delta.
    """
    svc = _make_service(window_s=300.0)
    clock = _Clock(datetime(2026, 2, 5, 14, 30, tzinfo=UTC))
    monkeypatch.setattr("orion.ingestion.service.datetime", clock)

    shared_id = "uwblake2b-late-push-1"

    # Poll delivers at t0; push is silent.
    await svc._record_flow_parity([], [_poll_event(shared_id)], "trace-1")

    # Push finally arrives 400s later — past the 300s window.
    clock.advance(400)
    await svc._record_flow_parity([await _push_event(shared_id)], [], "trace-2")
    row = await _latest_parity_row()
    assert row.matched_count == 0, "late push must not count as a match"
    assert row.missed_by_push_count == 1
    assert row.median_latency_improvement_s is None
    # Finalized: neither map retains the id, so it can't be recounted later.
    assert shared_id not in svc._push_seen
    assert shared_id not in svc._poll_seen
    clock.advance(400)
    await svc._record_flow_parity([], [], "trace-3")
    row = await _latest_parity_row()
    assert row.missed_by_push_count == 0
    assert row.missed_by_poll_count == 0


async def test_poll_arriving_past_window_is_missed_by_poll(monkeypatch):
    """Symmetric late-arrival case: push at t0, poll lands past the window →
    missed_by_poll, not matched."""
    svc = _make_service(window_s=300.0)
    clock = _Clock(datetime(2026, 2, 5, 14, 30, tzinfo=UTC))
    monkeypatch.setattr("orion.ingestion.service.datetime", clock)

    shared_id = "uwblake2b-late-poll-1"

    await svc._record_flow_parity([await _push_event(shared_id)], [], "trace-1")
    clock.advance(400)
    await svc._record_flow_parity([], [_poll_event(shared_id)], "trace-2")
    row = await _latest_parity_row()
    assert row.matched_count == 0
    assert row.missed_by_poll_count == 1
    assert row.missed_by_push_count == 0


async def test_poll_with_no_push_counts_missed_after_window(monkeypatch):
    """A poll id with no push delivery is missed_by_push once the window elapses."""
    svc = _make_service(window_s=300.0)
    clock = _Clock(datetime(2026, 2, 5, 14, 30, tzinfo=UTC))
    monkeypatch.setattr("orion.ingestion.service.datetime", clock)

    poll_id = "uwblake2b-orphan-1"

    # Cycle 1: poll delivers, push never does.
    await svc._record_flow_parity([], [_poll_event(poll_id)], "trace-1")
    assert (await _latest_parity_row()).missed_by_push_count == 0

    # Still inside the window a few cycles later → not yet finalized.
    clock.advance(120)
    await svc._record_flow_parity([], [], "trace-2")
    assert (await _latest_parity_row()).missed_by_push_count == 0

    # Window fully elapsed (>300s since first-seen) → finalized as missed_by_push.
    clock.advance(200)
    await svc._record_flow_parity([], [], "trace-3")
    row = await _latest_parity_row()
    assert row.missed_by_push_count == 1
    assert row.missed_by_push_ids == [poll_id]


async def test_uwflow_fallback_id_counted_unmatchable(monkeypatch):
    """An expired uwflow_* poll id is missed_by_push but also unmatchable."""
    svc = _make_service(window_s=300.0)
    clock = _Clock(datetime(2026, 2, 5, 14, 30, tzinfo=UTC))
    monkeypatch.setattr("orion.ingestion.service.datetime", clock)

    await svc._record_flow_parity([], [_poll_event("uwflow_deadbeef")], "trace-1")
    clock.advance(400)
    await svc._record_flow_parity([], [], "trace-2")
    row = await _latest_parity_row()
    assert row.missed_by_push_count == 1
    assert row.parity_unmatchable_count == 1


async def test_rolling_maps_stay_bounded(monkeypatch):
    """Over many cycles of churning ids, the rolling maps prune to the window."""
    svc = _make_service(window_s=120.0)
    clock = _Clock(datetime(2026, 2, 5, 14, 30, tzinfo=UTC))
    monkeypatch.setattr("orion.ingestion.service.datetime", clock)

    # 50 cycles, a fresh unique id each, 30s apart. Window is 120s, so at most
    # ~5 first-seen entries can survive per map at any time.
    for i in range(50):
        clock.advance(30)
        await svc._record_flow_parity(
            [await _push_event(f"push-{i}")],
            [_poll_event(f"poll-{i}")],
            f"trace-{i}",
        )

    assert len(svc._push_seen) <= 6
    assert len(svc._poll_seen) <= 6


async def test_push_and_poll_same_id_persists_once():
    """A push+poll duplicate pair (same event_id) yields exactly one bronze row."""
    shared_id = "uwblake2b-parity-1"
    push = await _push_event(shared_id)
    poll = _poll_event(shared_id)

    # Both paths produce the same id (the whole contract).
    assert push.event_id == poll.event_id == shared_id

    # Union, as shadow mode feeds the pipeline, deduped then persisted.
    async with async_session_factory() as session:
        deduper = DeduplicationEngine(session)
        deduped = await deduper.dedupe_batch([push, poll])

    assert len(deduped) == 1

    async with async_session_factory() as session:
        await persist_bronze_events(session, deduped)
        await session.commit()

    async with async_session_factory() as session:
        from sqlalchemy import func, select

        count = (
            await session.execute(select(func.count(BronzeEvent.event_id)).where(BronzeEvent.event_id == shared_id))
        ).scalar_one()
    assert count == 1


async def test_persist_is_idempotent_across_separate_cycles():
    """If push wins one cycle and poll arrives a later cycle, no second row.

    Simulates a fresh DeduplicationEngine per cycle (as ingestion creates one
    per cycle): the DB-backed check + ON CONFLICT still collapse the duplicate.
    """
    shared_id = "uwblake2b-parity-2"

    # Cycle 1: push arrives, persists.
    push = await _push_event(shared_id)
    async with async_session_factory() as session:
        deduper = DeduplicationEngine(session)
        deduped = await deduper.dedupe_batch([push])
        assert len(deduped) == 1
    async with async_session_factory() as session:
        await persist_bronze_events(session, deduped)
        await session.commit()

    # Cycle 2: poll arrives with the SAME id, fresh deduper — must be dropped.
    poll = _poll_event(shared_id)
    async with async_session_factory() as session:
        deduper = DeduplicationEngine(session)
        deduped2 = await deduper.dedupe_batch([poll])
    assert deduped2 == []

    async with async_session_factory() as session:
        from sqlalchemy import func, select

        count = (
            await session.execute(select(func.count(BronzeEvent.event_id)).where(BronzeEvent.event_id == shared_id))
        ).scalar_one()
    assert count == 1
