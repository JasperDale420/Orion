from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orion.storage.models import IngestWatermark


def _ensure_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


async def get_watermark(session: AsyncSession, *, key: str) -> Optional[datetime]:
    result = await session.execute(select(IngestWatermark).where(IngestWatermark.key == key))
    row = result.scalars().first()
    if row is None:
        return None
    return _ensure_utc(row.last_seen_ts_utc)


async def upsert_watermark(session: AsyncSession, *, key: str, last_seen_ts_utc: datetime) -> datetime:
    last_seen_ts_utc = _ensure_utc(last_seen_ts_utc)

    result = await session.execute(select(IngestWatermark).where(IngestWatermark.key == key))
    row = result.scalars().first()

    if row is None:
        row = IngestWatermark(key=key, last_seen_ts_utc=last_seen_ts_utc)
        session.add(row)
        await session.commit()
        return last_seen_ts_utc

    if row.last_seen_ts_utc is None or _ensure_utc(row.last_seen_ts_utc) < last_seen_ts_utc:
        row.last_seen_ts_utc = last_seen_ts_utc
        await session.commit()

    return _ensure_utc(row.last_seen_ts_utc)
