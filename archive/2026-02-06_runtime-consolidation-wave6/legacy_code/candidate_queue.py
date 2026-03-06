import asyncio
import logging

from orion.shared.patterns import AsyncSingleton

logger = logging.getLogger(__name__)


class CandidateQueue(AsyncSingleton):
    """
    Singleton in-memory queue for passing candidate IDs from ingestion to execution.

    This replaces expensive DB polling with a fast in-memory handoff.
    On restart, execution will backfill from DB for unprocessed candidates.
    """

    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=10000)
        self.dropped_count: int = 0
        logger.info("CandidateQueue initialized", extra={"event_type": "QUEUE_INIT"})

    async def push(self, candidate_id: str) -> None:
        """Push a candidate ID to the queue. Drops if full."""
        try:
            self.queue.put_nowait(candidate_id)
        except asyncio.QueueFull:
            self.dropped_count += 1
            if self.dropped_count % 100 == 0:
                logger.warning(
                    f"CandidateQueue full, dropped {self.dropped_count} candidates total",
                    extra={"event_type": "QUEUE_FULL", "dropped_total": self.dropped_count},
                )

    async def pop(self, timeout: float = 0.1) -> str | None:
        """Pop a candidate ID from the queue. Returns None if timeout."""
        try:
            return await asyncio.wait_for(self.queue.get(), timeout=timeout)
        except TimeoutError:
            return None

    def qsize(self) -> int:
        """Current queue depth."""
        return self.queue.qsize()
