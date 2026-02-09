"""
Backfill script for price_target_labels new columns.

Re-processes existing records to populate:
- Velocity: time_to_75/100/150_pct_seconds
- 0DTE checkpoints: 15m, 30m
- SWING/POSITION checkpoints: 8h, 1d, 2d, 3d, 1w

Usage:
    python -m orion.jobs.backfill_exit_columns [--batch-size 50] [--limit 1000]
"""

import argparse
import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from orion.main_price_target_labeler import (
    get_checkpoint_backfill_candidates as get_labeler_checkpoint_backfill_candidates,
)
from orion.main_price_target_labeler import (
    get_subsequent_prices as get_labeler_subsequent_prices,
)
from orion.main_price_target_labeler import (
    get_velocity_backfill_candidates as get_labeler_velocity_backfill_candidates,
)
from orion.shared.db_utils import db_query, db_write
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db
from orion.storage.watermarks import get_cursor_state, upsert_cursor_state
from sqlalchemy import text

logger = setup_struct_logger("orion.backfill.exit_columns")

BATCH_SIZE = 50
MAX_RECORD_RETRIES = 2
RETRY_SLEEP_SECONDS = 0.25
DEFAULT_DEAD_LETTER_PATH = os.getenv("ORION_BACKFILL_EXIT_DEAD_LETTER_PATH")
DEFAULT_DEAD_LETTER_MAX_BYTES = int(os.getenv("ORION_BACKFILL_EXIT_DEAD_LETTER_MAX_BYTES", "10485760"))
DEFAULT_DEAD_LETTER_REDACT_FIELDS = {
    field.strip()
    for field in os.getenv("ORION_BACKFILL_EXIT_DEAD_LETTER_REDACT_FIELDS", "").split(",")
    if field.strip()
}
VELOCITY_BACKFILL_CURSOR_KEY = "backfill_exit_columns.velocity.cursor"
CHECKPOINT_BACKFILL_CURSOR_KEY = "backfill_exit_columns.checkpoint.cursor"


async def _load_velocity_backfill_cursor() -> tuple[datetime | None, str | None]:
    """Load persisted velocity-phase resume cursor (timestamp + event_id)."""

    async def query(session: Any) -> tuple[datetime | None, str | None]:
        cursor = await get_cursor_state(session, VELOCITY_BACKFILL_CURSOR_KEY)
        if cursor is not None:
            return cursor.last_seen_ts_utc, cursor.last_seen_id
        return None, None

    return await db_query(query)


async def _save_velocity_backfill_cursor(entry_ts: datetime, event_id: str | None) -> None:
    """Persist velocity-phase cursor."""

    async def write(session: Any) -> None:
        await upsert_cursor_state(
            session,
            key=VELOCITY_BACKFILL_CURSOR_KEY,
            last_seen_ts_utc=entry_ts,
            last_seen_id=event_id,
        )

    await db_write(write)


async def _load_checkpoint_backfill_cursor() -> tuple[datetime | None, str | None]:
    """Load persisted checkpoint-phase resume cursor (timestamp + event_id)."""

    async def query(session: Any) -> tuple[datetime | None, str | None]:
        cursor = await get_cursor_state(session, CHECKPOINT_BACKFILL_CURSOR_KEY)
        if cursor is not None:
            return cursor.last_seen_ts_utc, cursor.last_seen_id
        return None, None

    return await db_query(query)


async def _save_checkpoint_backfill_cursor(entry_ts: datetime, event_id: str | None) -> None:
    """Persist checkpoint-phase cursor."""

    async def write(session: Any) -> None:
        await upsert_cursor_state(
            session,
            key=CHECKPOINT_BACKFILL_CURSOR_KEY,
            last_seen_ts_utc=entry_ts,
            last_seen_id=event_id,
        )

    await db_write(write)


def get_price_at_offset_minutes(prices: List[Dict[str, Any]], entry_ts: datetime, minutes: int) -> Optional[float]:
    """Get price at a specific minutes offset from entry."""
    target_ts = entry_ts + timedelta(minutes=minutes)
    closest = None
    min_diff = timedelta(minutes=5)

    for p in prices:
        diff = abs(p["ts"] - target_ts)
        if diff < min_diff:
            min_diff = diff
            closest = p["price"]
    return closest


def get_price_at_offset_hours(prices: List[Dict[str, Any]], entry_ts: datetime, hours: int) -> Optional[float]:
    """Get price at a specific hours offset from entry."""
    target_ts = entry_ts + timedelta(hours=hours)
    closest = None
    min_diff = timedelta(minutes=30)

    for p in prices:
        diff = abs(p["ts"] - target_ts)
        if diff < min_diff:
            min_diff = diff
            closest = p["price"]
    return closest


def get_price_at_offset_days(prices: List[Dict[str, Any]], entry_ts: datetime, days: int) -> Optional[float]:
    """Get price at a specific days offset from entry."""
    target_ts = entry_ts + timedelta(days=days)
    closest = None
    min_diff = timedelta(hours=4)

    for p in prices:
        diff = abs(p["ts"] - target_ts)
        if diff < min_diff:
            min_diff = diff
            closest = p["price"]
    return closest


async def get_records_to_backfill(
    limit: int = 1000,
    after_entry_ts: datetime | None = None,
    after_event_id: str | None = None,
) -> List[Dict[str, Any]]:
    """Get records missing new velocity columns via shared labeler helper."""
    return await get_labeler_velocity_backfill_candidates(
        limit=limit,
        after_entry_ts=after_entry_ts,
        after_event_id=after_event_id,
    )


async def get_all_records_for_checkpoints(
    limit: int = 1000,
    after_entry_ts: datetime | None = None,
    after_event_id: str | None = None,
) -> List[Dict[str, Any]]:
    """Get records missing checkpoint columns via shared labeler helper."""
    return await get_labeler_checkpoint_backfill_candidates(
        limit=limit,
        after_entry_ts=after_entry_ts,
        after_event_id=after_event_id,
    )


async def get_subsequent_prices(option_chain: str, entry_ts: datetime) -> List[Dict[str, Any]]:
    """Get subsequent prices via shared labeler helper."""
    return await get_labeler_subsequent_prices(option_chain, entry_ts)


async def update_velocity_columns(record: Dict[str, Any]) -> bool:
    """Update time-to-target velocity columns for a record."""
    event_id = record["event_id"]
    entry_ts = record["entry_ts"]

    updates = {}

    if record.get("hit_75_pct_ts"):
        updates["time_to_75_pct_seconds"] = int((record["hit_75_pct_ts"] - entry_ts).total_seconds())

    if record.get("hit_100_pct_ts"):
        updates["time_to_100_pct_seconds"] = int((record["hit_100_pct_ts"] - entry_ts).total_seconds())

    if record.get("hit_150_pct_ts"):
        updates["time_to_150_pct_seconds"] = int((record["hit_150_pct_ts"] - entry_ts).total_seconds())

    if not updates:
        return False

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    query = f"UPDATE price_target_labels SET {set_clause} WHERE event_id = :event_id"
    updates["event_id"] = event_id

    async def write(session: Any) -> None:
        await session.execute(text(query), updates)

    await db_write(write)
    return True


async def update_checkpoint_columns(record: Dict[str, Any]) -> bool:
    """Update bucket-specific checkpoint columns for a record."""
    event_id = record["event_id"]
    option_chain = record["option_chain"]
    entry_ts = record["entry_ts"]
    entry_price = record["entry_option_price"]

    if entry_price <= 0:
        return False

    prices = await get_subsequent_prices(option_chain, entry_ts)
    if not prices:
        return False

    updates: Dict[str, Any] = {}

    # 0DTE checkpoints (15m, 30m)
    price_15m = get_price_at_offset_minutes(prices, entry_ts, 15)
    price_30m = get_price_at_offset_minutes(prices, entry_ts, 30)

    if price_15m:
        updates["price_at_15m"] = price_15m
        updates["return_at_15m"] = ((price_15m - entry_price) / entry_price) * 100

    if price_30m:
        updates["price_at_30m"] = price_30m
        updates["return_at_30m"] = ((price_30m - entry_price) / entry_price) * 100

    # 8h checkpoint
    price_8h = get_price_at_offset_hours(prices, entry_ts, 8)
    if price_8h:
        updates["price_at_8h"] = price_8h
        updates["return_at_8h"] = ((price_8h - entry_price) / entry_price) * 100

    # Day checkpoints
    for days, suffix in [(1, "1d"), (2, "2d"), (3, "3d"), (7, "1w")]:
        price = get_price_at_offset_days(prices, entry_ts, days)
        if price:
            updates[f"price_at_{suffix}"] = price
            updates[f"return_at_{suffix}"] = ((price - entry_price) / entry_price) * 100

    if not updates:
        return False

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    query = f"UPDATE price_target_labels SET {set_clause} WHERE event_id = :event_id"
    updates["event_id"] = event_id

    async def write(session: Any) -> None:
        await session.execute(text(query), updates)

    await db_write(write)
    return True


async def _update_record_with_retry(
    record: Dict[str, Any],
    update_fn: Callable[[Dict[str, Any]], Awaitable[bool]],
    phase_name: str,
    max_retries: int = MAX_RECORD_RETRIES,
    retry_sleep_seconds: float = RETRY_SLEEP_SECONDS,
) -> tuple[bool, bool, int, str | None]:
    """Run a record updater with bounded retries.

    Returns:
        (updated, failed, retries_used, error_message)
    """
    retries_used = 0
    event_id = record.get("event_id")

    for attempt in range(max_retries + 1):
        try:
            updated = await update_fn(record)
            return updated, False, retries_used, None
        except Exception as exc:
            error_message = str(exc)
            if attempt >= max_retries:
                logger.error(
                    "Failed %s update for event_id=%s after %s attempt(s): %s",
                    phase_name,
                    event_id,
                    attempt + 1,
                    exc,
                )
                return False, True, retries_used, error_message

            retries_used += 1
            logger.warning(
                "Retrying %s update for event_id=%s after attempt %s/%s: %s",
                phase_name,
                event_id,
                attempt + 1,
                max_retries + 1,
                exc,
            )
            await asyncio.sleep(retry_sleep_seconds)

    return False, True, retries_used, "unknown error"


def _json_safe(value: Any) -> Any:
    """Best-effort JSON-safe conversion for dead-letter payloads."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _apply_dead_letter_redaction(payload: Dict[str, Any], redact_fields: set[str]) -> Dict[str, Any]:
    """Redact configured payload fields in dead-letter records."""
    if not redact_fields:
        return payload
    return {key: ("[REDACTED]" if key in redact_fields else value) for key, value in payload.items()}


def _rotate_dead_letter_file_if_needed(dead_letter_file: Path, max_bytes: int) -> bool:
    """Rotate dead-letter file when size exceeds threshold."""
    if max_bytes <= 0 or not dead_letter_file.exists():
        return False
    if dead_letter_file.stat().st_size < max_bytes:
        return False

    suffix = 1
    while True:
        candidate = dead_letter_file.with_name(f"{dead_letter_file.name}.{suffix}")
        if not candidate.exists():
            dead_letter_file.rename(candidate)
            return True
        suffix += 1


def _write_dead_letter_record(
    dead_letter_path: str,
    payload: Dict[str, Any],
    max_bytes: int = DEFAULT_DEAD_LETTER_MAX_BYTES,
    redact_fields: set[str] | None = None,
) -> bool:
    """Append a dead-letter payload as JSONL."""
    path = Path(dead_letter_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    redacted_payload = _apply_dead_letter_redaction(payload, redact_fields or set())
    rotated = _rotate_dead_letter_file_if_needed(path, max_bytes)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(redacted_payload, default=_json_safe) + "\n")
    return rotated


async def run_backfill(
    batch_size: int = BATCH_SIZE,
    limit: int = 1000,
    max_retries: int = MAX_RECORD_RETRIES,
    retry_sleep_seconds: float = RETRY_SLEEP_SECONDS,
    dead_letter_path: str | None = DEFAULT_DEAD_LETTER_PATH,
    dead_letter_max_bytes: int = DEFAULT_DEAD_LETTER_MAX_BYTES,
    dead_letter_redact_fields: set[str] | None = None,
) -> Dict[str, Any]:
    """Run the backfill job."""
    await init_db()
    dead_letter_redact_fields = dead_letter_redact_fields or DEFAULT_DEAD_LETTER_REDACT_FIELDS

    logger.info(
        "Starting backfill with batch_size=%s, limit=%s, max_retries=%s, retry_sleep_seconds=%s, dead_letter_path=%s, dead_letter_max_bytes=%s, dead_letter_redact_fields=%s",
        batch_size,
        limit,
        max_retries,
        retry_sleep_seconds,
        dead_letter_path,
        dead_letter_max_bytes,
        sorted(dead_letter_redact_fields),
    )

    # Phase 1: Velocity columns (fast, just uses existing timestamps)
    logger.info("Phase 1: Backfilling velocity columns (time_to_75/100/150_pct_seconds)...")
    velocity_updated = 0
    velocity_failed = 0
    velocity_retried = 0
    velocity_processed = 0
    velocity_dead_lettered = 0
    velocity_dead_letter_rotated = 0
    log_velocity_every = max(batch_size, 1)
    velocity_after_entry_ts, velocity_after_event_id = await _load_velocity_backfill_cursor()

    if velocity_after_entry_ts is not None:
        logger.info(
            "Resuming velocity backfill from persisted cursor ts=%s event_id=%s",
            velocity_after_entry_ts.isoformat(),
            velocity_after_event_id,
        )

    while True:
        remaining = limit - velocity_processed
        if remaining <= 0:
            break

        velocity_records = await get_records_to_backfill(
            limit=min(batch_size, remaining),
            after_entry_ts=velocity_after_entry_ts,
            after_event_id=velocity_after_event_id,
        )
        if not velocity_records:
            break

        for record in velocity_records:
            updated, failed, retries, error_message = await _update_record_with_retry(
                record,
                update_velocity_columns,
                phase_name="velocity",
                max_retries=max_retries,
                retry_sleep_seconds=retry_sleep_seconds,
            )
            if updated:
                velocity_updated += 1
            if failed:
                velocity_failed += 1
                if dead_letter_path:
                    rotated = _write_dead_letter_record(
                        dead_letter_path,
                        {
                            "phase": "velocity",
                            "event_id": record.get("event_id"),
                            "entry_ts": record.get("entry_ts"),
                            "error": error_message or "unknown error",
                            "retries_used": retries,
                        },
                        max_bytes=dead_letter_max_bytes,
                        redact_fields=dead_letter_redact_fields,
                    )
                    velocity_dead_lettered += 1
                    velocity_dead_letter_rotated += int(rotated)
            velocity_retried += retries

            velocity_processed += 1
            velocity_after_entry_ts = record.get("entry_ts")
            velocity_after_event_id = record.get("event_id")
            if velocity_after_entry_ts is not None:
                await _save_velocity_backfill_cursor(velocity_after_entry_ts, velocity_after_event_id)

            if velocity_processed % log_velocity_every == 0:
                logger.info(
                    "Processed %s velocity records | Updated: %s | Failed: %s | Retries: %s",
                    velocity_processed,
                    velocity_updated,
                    velocity_failed,
                    velocity_retried,
                )

            if velocity_processed >= limit:
                break

    logger.info(
        "Velocity columns updated: %s/%s | failed=%s | retries=%s | dead_lettered=%s",
        velocity_updated,
        velocity_processed,
        velocity_failed,
        velocity_retried,
        velocity_dead_lettered,
    )

    # Phase 2: Checkpoint columns (slower, needs to fetch price history)
    logger.info("Phase 2: Backfilling checkpoint columns (15m/30m/8h/1d/2d/3d/1w)...")
    checkpoint_updated = 0
    checkpoint_failed = 0
    checkpoint_retried = 0
    checkpoint_processed = 0
    checkpoint_dead_lettered = 0
    checkpoint_dead_letter_rotated = 0
    checkpoint_after_entry_ts, checkpoint_after_event_id = await _load_checkpoint_backfill_cursor()
    log_checkpoint_every = max(batch_size, 1)

    if checkpoint_after_entry_ts is not None:
        logger.info(
            "Resuming checkpoint backfill from persisted cursor ts=%s event_id=%s",
            checkpoint_after_entry_ts.isoformat(),
            checkpoint_after_event_id,
        )

    while True:
        remaining = limit - checkpoint_processed
        if remaining <= 0:
            break

        checkpoint_records = await get_all_records_for_checkpoints(
            limit=min(batch_size, remaining),
            after_entry_ts=checkpoint_after_entry_ts,
            after_event_id=checkpoint_after_event_id,
        )
        if not checkpoint_records:
            break

        for record in checkpoint_records:
            updated, failed, retries, error_message = await _update_record_with_retry(
                record,
                update_checkpoint_columns,
                phase_name="checkpoint",
                max_retries=max_retries,
                retry_sleep_seconds=retry_sleep_seconds,
            )
            if updated:
                checkpoint_updated += 1
            if failed:
                checkpoint_failed += 1
                if dead_letter_path:
                    rotated = _write_dead_letter_record(
                        dead_letter_path,
                        {
                            "phase": "checkpoint",
                            "event_id": record.get("event_id"),
                            "entry_ts": record.get("entry_ts"),
                            "error": error_message or "unknown error",
                            "retries_used": retries,
                        },
                        max_bytes=dead_letter_max_bytes,
                        redact_fields=dead_letter_redact_fields,
                    )
                    checkpoint_dead_lettered += 1
                    checkpoint_dead_letter_rotated += int(rotated)
            checkpoint_retried += retries

            checkpoint_processed += 1
            checkpoint_after_entry_ts = record.get("entry_ts")
            checkpoint_after_event_id = record.get("event_id")
            if checkpoint_after_entry_ts is not None:
                await _save_checkpoint_backfill_cursor(checkpoint_after_entry_ts, checkpoint_after_event_id)

            if checkpoint_processed % log_checkpoint_every == 0:
                logger.info(
                    "Processed %s checkpoint records | Updated: %s | Failed: %s | Retries: %s",
                    checkpoint_processed,
                    checkpoint_updated,
                    checkpoint_failed,
                    checkpoint_retried,
                )

            if checkpoint_processed >= limit:
                break

    logger.info(
        "Checkpoint columns updated: %s/%s | failed=%s | retries=%s | dead_lettered=%s",
        checkpoint_updated,
        checkpoint_processed,
        checkpoint_failed,
        checkpoint_retried,
        checkpoint_dead_lettered,
    )

    summary = {
        "velocity": {
            "processed": velocity_processed,
            "updated": velocity_updated,
            "failed": velocity_failed,
            "retried": velocity_retried,
            "dead_lettered": velocity_dead_lettered,
            "dead_letter_rotated": velocity_dead_letter_rotated,
        },
        "checkpoint": {
            "processed": checkpoint_processed,
            "updated": checkpoint_updated,
            "failed": checkpoint_failed,
            "retried": checkpoint_retried,
            "dead_lettered": checkpoint_dead_lettered,
            "dead_letter_rotated": checkpoint_dead_letter_rotated,
        },
        "total_processed": velocity_processed + checkpoint_processed,
        "total_updated": velocity_updated + checkpoint_updated,
        "total_failed": velocity_failed + checkpoint_failed,
        "total_retried": velocity_retried + checkpoint_retried,
        "total_dead_lettered": velocity_dead_lettered + checkpoint_dead_lettered,
        "total_dead_letter_rotated": velocity_dead_letter_rotated + checkpoint_dead_letter_rotated,
        "dead_letter_path": dead_letter_path,
        "dead_letter_max_bytes": dead_letter_max_bytes,
        "dead_letter_redact_fields": sorted(dead_letter_redact_fields),
    }
    logger.info("Backfill complete! summary=%s", summary)
    return summary


async def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Backfill exit classifier columns")
    parser.add_argument("--batch-size", type=int, default=50, help="Batch size for logging")
    parser.add_argument("--limit", type=int, default=1000, help="Max records to process")
    parser.add_argument(
        "--max-retries",
        type=int,
        default=MAX_RECORD_RETRIES,
        help="Max retries per record update before marking failed",
    )
    parser.add_argument(
        "--retry-sleep-seconds",
        type=float,
        default=RETRY_SLEEP_SECONDS,
        help="Sleep interval between per-record retry attempts",
    )
    parser.add_argument(
        "--dead-letter-path",
        type=str,
        default=DEFAULT_DEAD_LETTER_PATH,
        help="Optional JSONL file path for exhausted-retry records",
    )
    parser.add_argument(
        "--dead-letter-max-bytes",
        type=int,
        default=DEFAULT_DEAD_LETTER_MAX_BYTES,
        help="Rotate dead-letter file after this size threshold",
    )
    parser.add_argument(
        "--dead-letter-redact-fields",
        type=str,
        default=",".join(sorted(DEFAULT_DEAD_LETTER_REDACT_FIELDS)),
        help="Comma-separated dead-letter payload fields to redact",
    )
    args = parser.parse_args()
    redact_fields = {field.strip() for field in args.dead_letter_redact_fields.split(",") if field.strip()}

    await run_backfill(
        batch_size=args.batch_size,
        limit=args.limit,
        max_retries=args.max_retries,
        retry_sleep_seconds=args.retry_sleep_seconds,
        dead_letter_path=args.dead_letter_path,
        dead_letter_max_bytes=args.dead_letter_max_bytes,
        dead_letter_redact_fields=redact_fields,
    )


if __name__ == "__main__":
    asyncio.run(main())
