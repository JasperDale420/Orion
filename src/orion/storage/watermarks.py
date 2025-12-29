from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orion.shared.utils import ensure_utc
from orion.storage.models import IngestWatermark


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
