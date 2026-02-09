from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orion.shared.utils import ensure_utc
from orion.storage.models import IngestWatermark, JobCursorState


@dataclass(frozen=True)
class CursorState:
    last_seen_ts_utc: datetime
    last_seen_id: str | None


def _is_newer_cursor(
    existing_ts: datetime | None,
    existing_id: str | None,
    incoming_ts: datetime,
    incoming_id: str | None,
) -> bool:
    if existing_ts is None:
        return True
    if existing_ts < incoming_ts:
        return True
    if existing_ts > incoming_ts:
        return False
    if incoming_id is None:
        return False
    if existing_id is None:
        return True
    return existing_id < incoming_id


async def get_watermark(session: AsyncSession, key: str) -> datetime | None:
    """Retrieve the latest watermark timestamp for the given key."""
    stmt = select(IngestWatermark).where(IngestWatermark.key == key)
    result = await session.execute(stmt)
    wm = result.scalars().first()
    return ensure_utc(wm.last_seen_ts_utc) if wm else None


async def upsert_watermark(session: AsyncSession, key: str, last_seen_ts_utc: datetime) -> datetime:
    """Update or insert a watermark for the given key."""
    last_seen_ts_utc = ensure_utc(last_seen_ts_utc)

    stmt = select(IngestWatermark).where(IngestWatermark.key == key)
    result = await session.execute(stmt)
    row = result.scalars().first()

    if row is None:
        row = IngestWatermark(key=key, last_seen_ts_utc=last_seen_ts_utc)
        session.add(row)
        return last_seen_ts_utc

    if row.last_seen_ts_utc is None or ensure_utc(row.last_seen_ts_utc) < last_seen_ts_utc:
        row.last_seen_ts_utc = last_seen_ts_utc

    result_ts = ensure_utc(row.last_seen_ts_utc)
    if result_ts is None:
        return last_seen_ts_utc
    return result_ts


async def get_cursor_state(session: AsyncSession, key: str) -> CursorState | None:
    """Retrieve the latest cursor state for the given key."""
    stmt = select(JobCursorState).where(JobCursorState.key == key)
    result = await session.execute(stmt)
    row = result.scalars().first()
    if row is None:
        return None
    ts_utc = ensure_utc(row.last_seen_ts_utc)
    if ts_utc is None:
        return None
    return CursorState(last_seen_ts_utc=ts_utc, last_seen_id=row.last_seen_id)


async def upsert_cursor_state(
    session: AsyncSession,
    key: str,
    last_seen_ts_utc: datetime,
    last_seen_id: str | None,
) -> CursorState:
    """Update or insert a cursor state for the given key."""
    incoming_ts = ensure_utc(last_seen_ts_utc)
    if incoming_ts is None:
        raise ValueError("last_seen_ts_utc must be timezone-aware")

    stmt = select(JobCursorState).where(JobCursorState.key == key)
    result = await session.execute(stmt)
    row = result.scalars().first()

    if row is None:
        row = JobCursorState(
            key=key,
            last_seen_ts_utc=incoming_ts,
            last_seen_id=last_seen_id,
        )
        session.add(row)
        return CursorState(last_seen_ts_utc=incoming_ts, last_seen_id=last_seen_id)

    existing_ts = ensure_utc(row.last_seen_ts_utc)
    if _is_newer_cursor(existing_ts, row.last_seen_id, incoming_ts, last_seen_id):
        row.last_seen_ts_utc = incoming_ts
        row.last_seen_id = last_seen_id

    result_ts = ensure_utc(row.last_seen_ts_utc)
    if result_ts is None:
        result_ts = incoming_ts
    return CursorState(last_seen_ts_utc=result_ts, last_seen_id=row.last_seen_id)
