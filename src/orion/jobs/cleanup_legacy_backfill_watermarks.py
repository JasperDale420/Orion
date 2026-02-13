from __future__ import annotations

import argparse
import asyncio
from typing import NoReturn

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from orion.shared.db_utils import db_query, db_write
from orion.shared.logger import setup_struct_logger
from orion.storage.models import IngestWatermark
from orion.storage.watermarks import delete_watermarks

logger = setup_struct_logger("orion.cleanup.legacy_backfill_watermarks")

LEGACY_BACKFILL_WATERMARK_KEYS: tuple[str, ...] = (
    "backfill_exit_columns.velocity",
    "backfill_exit_columns.velocity.cursor",
    "backfill_exit_columns.checkpoint",
    "backfill_exit_columns.checkpoint.cursor",
)


async def _count_legacy_backfill_watermarks(session: AsyncSession) -> int:
    stmt = (
        select(func.count()).select_from(IngestWatermark).where(IngestWatermark.key.in_(LEGACY_BACKFILL_WATERMARK_KEYS))
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def cleanup_legacy_backfill_watermarks(*, dry_run: bool = False) -> int:
    """Cleanup obsolete legacy watermark keys for retired backfill resume paths."""
    if dry_run:
        count = await db_query(_count_legacy_backfill_watermarks)
        logger.info("Legacy watermark dry-run complete", keys=LEGACY_BACKFILL_WATERMARK_KEYS, matching_rows=count)
        return count

    async def _delete(session: AsyncSession) -> int:
        return await delete_watermarks(session, LEGACY_BACKFILL_WATERMARK_KEYS)

    deleted = await db_write(_delete)
    logger.info("Legacy watermark cleanup complete", keys=LEGACY_BACKFILL_WATERMARK_KEYS, deleted_rows=deleted)
    return deleted


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cleanup obsolete backfill watermark rows.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only count matching rows without deleting.",
    )
    return parser.parse_args()


async def _main_async() -> int:
    args = _parse_args()
    return await cleanup_legacy_backfill_watermarks(dry_run=args.dry_run)


def main() -> NoReturn:
    deleted_or_count = asyncio.run(_main_async())
    raise SystemExit(0 if deleted_or_count >= 0 else 1)


if __name__ == "__main__":
    main()
