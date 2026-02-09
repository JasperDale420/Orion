"""
SQLite contention soak harness for Orion DB transaction retries.

Runs concurrent writes against a local SQLite database and reports:
- attempted writes
- successful writes
- failed writes
- final counter value
- elapsed seconds
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from orion.shared.db_utils import db_query, db_write
from orion.storage import db as db_module
from sqlalchemy import text

SOAK_TABLE = "orion_soak_counter"


async def _setup_soak_table() -> None:
    async def _write(session: Any) -> None:
        await session.execute(text("PRAGMA journal_mode=WAL"))
        await session.execute(text("PRAGMA busy_timeout=5000"))
        await session.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {SOAK_TABLE} (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    value INTEGER NOT NULL
                )
            """
            )
        )
        await session.execute(text(f"INSERT OR IGNORE INTO {SOAK_TABLE} (id, value) VALUES (1, 0)"))

    await db_write(_write)


async def _read_counter() -> int:
    async def _query(session: Any) -> int:
        result = await session.execute(text(f"SELECT value FROM {SOAK_TABLE} WHERE id = 1"))
        row = result.fetchone()
        return int(row[0]) if row else 0

    return await db_query(_query)


async def run_sqlite_contention_soak(
    *,
    db_path: str,
    workers: int,
    iterations_per_worker: int,
    hold_lock_ms: int,
) -> dict[str, Any]:
    db_file = Path(db_path).expanduser().resolve()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite+aiosqlite:///{db_file}"

    original_db_url = str(db_module.engine.url)
    db_module.configure_db(db_url, echo=False)
    try:
        await _setup_soak_table()
        lock_hold_seconds = max(0.0, float(hold_lock_ms) / 1000.0)

        successes = 0
        failures = 0
        counter_lock = asyncio.Lock()

        async def _worker() -> None:
            nonlocal successes, failures
            for _ in range(iterations_per_worker):
                try:

                    async def _increment(session: Any) -> None:
                        await session.execute(text(f"UPDATE {SOAK_TABLE} SET value = value + 1 WHERE id = 1"))
                        if lock_hold_seconds > 0:
                            await asyncio.sleep(lock_hold_seconds)

                    await db_write(_increment)
                    async with counter_lock:
                        successes += 1
                except Exception:
                    async with counter_lock:
                        failures += 1

        started = time.perf_counter()
        await asyncio.gather(*[_worker() for _ in range(max(1, workers))])
        elapsed = time.perf_counter() - started
        final_value = await _read_counter()
        attempted = max(1, workers) * iterations_per_worker

        return {
            "db_url": db_url,
            "workers": max(1, workers),
            "iterations_per_worker": iterations_per_worker,
            "hold_lock_ms": hold_lock_ms,
            "attempted_writes": attempted,
            "successful_writes": successes,
            "failed_writes": failures,
            "final_counter_value": final_value,
            "elapsed_seconds": elapsed,
        }
    finally:
        db_module.configure_db(original_db_url, echo=False)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run SQLite contention soak against Orion db_transaction helpers.")
    parser.add_argument("--db-path", default="./artifacts/sqlite-contention-soak.db")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--iterations-per-worker", type=int, default=200)
    parser.add_argument("--hold-lock-ms", type=int, default=5)
    return parser


async def _run_cli() -> None:
    args = _build_parser().parse_args()
    summary = await run_sqlite_contention_soak(
        db_path=args.db_path,
        workers=args.workers,
        iterations_per_worker=args.iterations_per_worker,
        hold_lock_ms=args.hold_lock_ms,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_run_cli())
