import logging
from collections import OrderedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orion.storage.models import BronzeEvent

logger = logging.getLogger(__name__)

# Cap on the in-memory seen-id cache. Over a multi-day session the DB-backed
# dedupe check sees millions of event_ids; an unbounded set would grow for the
# process lifetime. The cache is purely a read-side optimization over the DB
# (the source of truth), so FIFO eviction is safe: an evicted id that reappears
# falls through to the DB query and is still deduped.
SEEN_IDS_CACHE_CAP = 200_000


class DeduplicationEngine:
    """
    Enforces idempotency (PRD 4.1, 7.1). Async compatible.
    """

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session
        # OrderedDict used as a bounded FIFO ring; keys are seen event_ids,
        # values are unused. Oldest insert is evicted first when over cap.
        self._seen_ids_cache: OrderedDict[str, None] = OrderedDict()

    def _remember(self, event_id: str) -> None:
        """Record a seen event_id, evicting the oldest entry when over cap."""
        if event_id in self._seen_ids_cache:
            return
        self._seen_ids_cache[event_id] = None
        if len(self._seen_ids_cache) > SEEN_IDS_CACHE_CAP:
            self._seen_ids_cache.popitem(last=False)

    async def is_duplicate(self, event_id: str) -> bool:
        """
        Check if event_id exists in cache or DB.
        """
        if event_id in self._seen_ids_cache:
            return True

        # Check DB
        result = await self.db_session.execute(select(BronzeEvent).filter_by(event_id=event_id))
        exists = result.scalar_one_or_none() is not None

        if exists:
            self._remember(event_id)
            return True

        return False

    async def dedupe_batch(self, events: list[BronzeEvent]) -> list[BronzeEvent]:
        """
        Filters a batch of BronzeEvents, returning only new ones.
        Updates cache for accepted events.
        Optimized to use bulk IN clause queries.
        """
        if not events:
            return []

        # 1. Dedupe within the batch and check local cache
        unique_candidates = {}
        for evt in events:
            if evt.event_id not in self._seen_ids_cache:
                unique_candidates[evt.event_id] = evt  # Last write wins within batch if dupes exist

        if not unique_candidates:
            return []

        candidate_ids = list(unique_candidates.keys())
        existing_in_db = set()

        # 2. Bulk Check DB in chunks to avoid parameter limit
        chunk_size = 3000
        for i in range(0, len(candidate_ids), chunk_size):
            chunk = candidate_ids[i : i + chunk_size]
            result = await self.db_session.execute(select(BronzeEvent.event_id).where(BronzeEvent.event_id.in_(chunk)))
            for found_id in result.scalars():
                existing_in_db.add(found_id)
                self._remember(found_id)

        # 3. Filter
        final_events = []
        for eid, evt in unique_candidates.items():
            if eid not in existing_in_db:
                final_events.append(evt)
                self._remember(eid)
            else:
                logger.debug(f"Duplicate event dropped: {eid}")

        return final_events
