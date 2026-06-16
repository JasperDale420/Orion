"""Tests for db.wait_for_db — bounded retry-with-backoff on the startup DB connect.

Startup used to call init_db() directly; a transient DB outage then exited the
process and launchd crash-looped it on a 30s throttle (observed 2026-06-07), and
for ingestion left a degraded start pinned to the static watchlist all session
(2026-06-01). wait_for_db polls SELECT 1 until the DB is reachable, then fails
loudly after a bounded number of attempts.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///:memory:")

import orion.storage.db as db


@pytest.mark.asyncio
async def test_returns_immediately_when_db_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: the probe succeeds on the first attempt → no backoff sleep.
    Uses the real probe against the in-memory engine to also exercise SELECT 1."""
    sleep = AsyncMock()
    monkeypatch.setattr(db.asyncio, "sleep", sleep)

    await db.wait_for_db(max_attempts=3)

    sleep.assert_not_called()


@pytest.mark.asyncio
async def test_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Probe fails twice (transient outage) then succeeds → returns, backing off
    exponentially between the two failures."""
    sleeps: list[float] = []
    monkeypatch.setattr(db.asyncio, "sleep", AsyncMock(side_effect=lambda d: sleeps.append(d)))
    probe = AsyncMock(side_effect=[OSError("refused"), OSError("refused"), None])
    monkeypatch.setattr(db, "_check_db_connection", probe)

    await db.wait_for_db(max_attempts=5, base_delay=1.0, max_delay=10.0)

    assert probe.await_count == 3
    assert sleeps == [1.0, 2.0]


@pytest.mark.asyncio
async def test_raises_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuinely-down DB surfaces loudly after the bounded retries."""
    monkeypatch.setattr(db.asyncio, "sleep", AsyncMock())
    probe = AsyncMock(side_effect=OSError("connection refused"))
    monkeypatch.setattr(db, "_check_db_connection", probe)

    with pytest.raises(RuntimeError, match="not reachable after 3 attempts"):
        await db.wait_for_db(max_attempts=3)

    assert probe.await_count == 3


@pytest.mark.asyncio
async def test_aborts_immediately_if_cancel_already_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """A shutdown already requested before the wait → abort before any probe."""
    cancel = asyncio.Event()
    cancel.set()
    probe = AsyncMock()
    monkeypatch.setattr(db, "_check_db_connection", probe)

    with pytest.raises(RuntimeError, match="aborted: shutdown requested"):
        await db.wait_for_db(max_attempts=5, cancel_event=cancel)

    probe.assert_not_awaited()


@pytest.mark.asyncio
async def test_aborts_when_cancel_set_during_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """A SIGTERM during the DB wait aborts promptly instead of blocking for the
    full backoff (the operational-shutdown regression the review flagged)."""
    cancel = asyncio.Event()

    async def _probe_then_signal() -> None:
        cancel.set()  # simulate the shutdown signal arriving during the connect
        raise OSError("refused")

    monkeypatch.setattr(db, "_check_db_connection", _probe_then_signal)

    with pytest.raises(RuntimeError, match="aborted: shutdown requested"):
        await db.wait_for_db(max_attempts=10, base_delay=0.01, cancel_event=cancel)
