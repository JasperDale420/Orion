"""Bounded-cache tests for DeduplicationEngine.

Guards audit finding #10: ``_seen_ids_cache`` must not accumulate every
event_id for the process lifetime. It is now a FIFO ring capped at
``SEEN_IDS_CACHE_CAP``. Correctness requirement: the DB is the source of
truth, so an id evicted from the cache must still be deduped via the DB path.

Uses the in-memory SQLite DB provided by the autouse ``setup_test_db`` fixture
in tests/conftest.py.
"""

from datetime import UTC, datetime

import pytest

from orion.processing import deduper as deduper_mod
from orion.processing.deduper import DeduplicationEngine
from orion.storage.db import async_session_factory
from orion.storage.models import BronzeEvent


def _event(event_id: str) -> BronzeEvent:
    ts = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
    return BronzeEvent(
        event_id=event_id,
        source="UW",
        source_event_id=None,
        event_type="UW_FLOW",
        ticker="SPY",
        session="REGULAR",
        event_ts_utc=ts,
        received_ts_utc=ts,
        payload={"ticker": "SPY"},
        ingest={},
    )


@pytest.mark.asyncio
async def test_seen_cache_respects_capacity(monkeypatch):
    """The seen-id cache never exceeds the configured cap, evicting FIFO."""
    monkeypatch.setattr(deduper_mod, "SEEN_IDS_CACHE_CAP", 10)

    async with async_session_factory() as session:
        engine = DeduplicationEngine(session)
        for i in range(25):
            engine._remember(f"id_{i}")

        assert len(engine._seen_ids_cache) == 10
        # Oldest (FIFO) ids evicted, newest retained.
        assert "id_0" not in engine._seen_ids_cache
        assert "id_14" not in engine._seen_ids_cache
        assert "id_15" in engine._seen_ids_cache
        assert "id_24" in engine._seen_ids_cache


@pytest.mark.asyncio
async def test_evicted_id_still_deduped_via_db(monkeypatch):
    """A duplicate event whose id was evicted from the cache is still detected
    as a duplicate because the DB query is the source of truth."""
    monkeypatch.setattr(deduper_mod, "SEEN_IDS_CACHE_CAP", 5)

    async with async_session_factory() as session:
        engine = DeduplicationEngine(session)

        # Persist event "evt_dup" to the DB (the source of truth).
        session.add(_event("evt_dup"))
        await session.commit()

        # First check populates the cache from the DB.
        assert await engine.is_duplicate("evt_dup") is True
        assert "evt_dup" in engine._seen_ids_cache

        # Overflow the cache so "evt_dup" is evicted (it was inserted first).
        for i in range(10):
            engine._remember(f"filler_{i}")
        assert "evt_dup" not in engine._seen_ids_cache

        # Despite cache eviction, the duplicate is still caught via the DB.
        assert await engine.is_duplicate("evt_dup") is True


@pytest.mark.asyncio
async def test_dedupe_batch_drops_evicted_db_duplicate(monkeypatch):
    """dedupe_batch must drop an already-persisted event even when its id is no
    longer in the in-memory cache."""
    monkeypatch.setattr(deduper_mod, "SEEN_IDS_CACHE_CAP", 5)

    async with async_session_factory() as session:
        engine = DeduplicationEngine(session)

        session.add(_event("persisted"))
        await session.commit()

        # Evict any trace from the cache (it starts empty, but be explicit).
        for i in range(10):
            engine._remember(f"noise_{i}")
        assert "persisted" not in engine._seen_ids_cache

        # Batch contains the already-persisted event plus a genuinely new one.
        result = await engine.dedupe_batch([_event("persisted"), _event("fresh")])

        returned_ids = {e.event_id for e in result}
        assert "persisted" not in returned_ids  # DB-deduped despite cache miss
        assert "fresh" in returned_ids
