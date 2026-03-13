from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from orion.processing.deduper import DeduplicationEngine
from orion.processing.normalizer import NormalizationEngine
from orion.storage.models import BronzeEvent


async def ingest_bronze_events(
    session: AsyncSession,
    events: Iterable[BronzeEvent],
    *,
    run_id: str,
    trace_id: str,
) -> list[BronzeEvent]:
    """
    Shared ingestion path used by live ingestion and DLQ replay.
    Ensures deterministic normalization, trading_date/session derivation, and idempotent writes (dedupe).
    Returns the unique, normalized events.
    """
    from orion.core.timekeeping import derive_trading_date_and_session

    normalized_events: list[BronzeEvent] = []
    deduper = DeduplicationEngine(session)

    for e in events:
        # Normalize payload (may raise; caller decides DLQ handling).
        e.payload = NormalizationEngine.normalize_event(e.source, e.event_type, e.payload)

        if not e.ticker:
            e.ticker = e.payload.get("ticker")

        if e.event_ts_utc and e.event_ts_utc.tzinfo is None:
            e.event_ts_utc = e.event_ts_utc.replace(tzinfo=UTC)

        if e.event_ts_utc:
            if e.session is None:
                td, sess = derive_trading_date_and_session(e.event_ts_utc)
                e.trading_date = td
                e.session = sess
            if e.trading_date is None:
                td, _ = derive_trading_date_and_session(e.event_ts_utc)
                e.trading_date = td

        if e.session is None:
            e.session = "CLOSED"

        if getattr(e, "ingest", None):
            e.ingest.setdefault("run_id", run_id)
            e.ingest.setdefault("trace_id", trace_id)
            e.ingest.setdefault("attempt", 1)
        else:
            e.ingest = {"connector": "dlq_replay", "run_id": run_id, "trace_id": trace_id, "attempt": 1}

        if e.received_ts_utc is None:
            e.received_ts_utc = datetime.now(UTC)

        normalized_events.append(e)

    unique_events = await deduper.dedupe_batch(normalized_events)
    return unique_events
