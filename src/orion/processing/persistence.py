from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from orion.shared.utils import make_json_safe
from orion.storage.models import BronzeEvent
from orion.storage.models_gold import CandidateTrade
from orion.storage.models_silver import SilverSignal

logger = logging.getLogger(__name__)


async def persist_bronze_events(session: AsyncSession, events: list[BronzeEvent]) -> None:
    if not events:
        return

    values = []
    for e in events:
        values.append(
            {
                "event_id": e.event_id,
                "source": e.source,
                "source_event_id": getattr(e, "source_event_id", None),
                "event_type": e.event_type,
                "ticker": e.ticker,
                "trading_date": getattr(e, "trading_date", None),
                "session": e.session,
                "schema_version": getattr(e, "schema_version", None) or "v1",
                "event_ts_utc": e.event_ts_utc,
                "received_ts_utc": e.received_ts_utc,
                "payload": make_json_safe(e.payload),
                "ingest": make_json_safe(getattr(e, "ingest", None) or {}),
            }
        )

    BATCH_SIZE = 1000  # noqa: N806
    for i in range(0, len(values), BATCH_SIZE):
        batch = values[i : i + BATCH_SIZE]
        stmt = insert(BronzeEvent).values(batch)
        stmt = stmt.on_conflict_do_nothing(index_elements=["event_id"])
        await session.execute(stmt)


async def persist_silver_from_bronze(session: AsyncSession, events: list[BronzeEvent]) -> None:
    if not events:
        return
    logger.info("Skipping local Silver materialization; Heber is the canonical Silver source")


async def persist_silver_signals(session: AsyncSession, signals: list[SilverSignal]) -> None:
    if not signals:
        return

    values = []
    for s in signals:
        values.append(
            {
                "signal_id": s.signal_id,
                "ticker": s.ticker,
                "signal_ts_utc": s.signal_ts_utc,
                "signal_type": s.signal_type,
                "features": make_json_safe(s.features),
                "created_at_utc": datetime.now(UTC),
            }
        )

    BATCH_SIZE = 1000  # noqa: N806
    for i in range(0, len(values), BATCH_SIZE):
        batch = values[i : i + BATCH_SIZE]
        stmt = insert(SilverSignal).values(batch)
        stmt = stmt.on_conflict_do_nothing(index_elements=["signal_id"])
        await session.execute(stmt)


async def persist_candidates(session: AsyncSession, candidates: list[CandidateTrade]) -> None:
    if not candidates:
        return

    values = []
    for c in candidates:
        values.append(
            {
                "candidate_id": c.candidate_id,
                "ticker": c.ticker,
                "timestamp_utc": c.timestamp_utc,
                "rule_id": c.rule_id,
                "direction": c.direction,
                "confidence": c.confidence,
                "source": c.source,
                "execution_params": make_json_safe(c.execution_params),
                "evidence": make_json_safe(c.evidence),
                "created_at_utc": c.created_at_utc or datetime.now(UTC),
            }
        )

    BATCH_SIZE = 1000  # noqa: N806
    for i in range(0, len(values), BATCH_SIZE):
        batch = values[i : i + BATCH_SIZE]
        stmt = insert(CandidateTrade).values(batch)
        stmt = stmt.on_conflict_do_nothing(index_elements=["candidate_id"])
        await session.execute(stmt)
