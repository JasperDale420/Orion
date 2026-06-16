"""Smoke tests for orion.jobs.nightly_backfill.

Audit #21 coverage. nightly_backfill is a thin orchestrator: run_nightly_backfill
delegates to backfill_ml_features.run_backfill, and the scheduling helpers
(is_trading_day / get_next_run_time) compute the next session-aware run time.
We mock the ML backfill (its real work reads Heber) and assert delegation and
the documented error-handling contract.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from orion.jobs import nightly_backfill


@pytest.mark.asyncio
async def test_run_nightly_backfill_invokes_ml_backfill_with_limits() -> None:
    """Happy path: the orchestrator calls run_ml_backfill once with the
    configured batch_size/limit (nightly_backfill.py line 86)."""
    with patch.object(nightly_backfill, "run_ml_backfill", new=AsyncMock()) as mock_ml:
        await nightly_backfill.run_nightly_backfill()

    mock_ml.assert_awaited_once_with(batch_size=100, limit=10000)


@pytest.mark.asyncio
async def test_run_nightly_backfill_swallows_ml_error() -> None:
    """Failure path: this is an unattended scheduled job. run_nightly_backfill
    wraps the work in try/except (lines 83-92) that logs via
    ``logger.error(..., exc_info=True)`` and returns normally - it must NOT
    propagate, so the daily loop in main() keeps running."""
    with patch.object(
        nightly_backfill,
        "run_ml_backfill",
        new=AsyncMock(side_effect=RuntimeError("heber down")),
    ):
        # Must not raise.
        await nightly_backfill.run_nightly_backfill()


def test_is_trading_day_weekend_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the market calendar is unavailable (RuntimeError), is_trading_day
    falls back to weekday-only logic (lines 41-43)."""
    monkeypatch.setattr(
        nightly_backfill._MARKET_SCHEDULE,
        "get_open_close",
        lambda dt: (_ for _ in ()).throw(RuntimeError("no calendar")),
    )
    saturday = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)  # Saturday
    monday = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)  # Monday
    assert nightly_backfill.is_trading_day(saturday) is False
    assert nightly_backfill.is_trading_day(monday) is True


def test_get_next_run_time_is_future_and_offset_from_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_next_run_time returns a future timestamp = session close + delay.
    With a stubbed calendar it must be after 'now' and carry the configured
    BACKFILL_DELAY_MINUTES offset."""

    def fake_open_close(dt: datetime) -> tuple[datetime, datetime]:
        # Always a session that closes at 21:00 UTC on the given day.
        close = dt.replace(hour=21, minute=0, second=0, microsecond=0)
        return close - timedelta(hours=6, minutes=30), close

    monkeypatch.setattr(nightly_backfill._MARKET_SCHEDULE, "get_open_close", fake_open_close)

    run_time = nightly_backfill.get_next_run_time()
    assert run_time > datetime.now(UTC)
    assert run_time.minute == nightly_backfill.BACKFILL_DELAY_MINUTES % 60
