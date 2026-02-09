from __future__ import annotations

import pytest
from orion.jobs.sqlite_contention_soak import run_sqlite_contention_soak


@pytest.mark.asyncio
async def test_sqlite_contention_soak_reports_consistent_totals(tmp_path) -> None:
    db_path = tmp_path / "sqlite_soak.db"

    summary = await run_sqlite_contention_soak(
        db_path=str(db_path),
        workers=3,
        iterations_per_worker=10,
        hold_lock_ms=1,
    )

    assert summary["attempted_writes"] == 30
    assert summary["successful_writes"] + summary["failed_writes"] == 30
    assert summary["final_counter_value"] == summary["successful_writes"]
