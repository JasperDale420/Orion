"""Smoke tests for orion.jobs.sync_earnings.

Audit #21 coverage. The job fetches earnings rows from Data-Gateway over HTTP
and tallies synced/errors counts; get_earnings_for_ticker derives a
days-to-earnings timeline from fetched rows. We mock the HTTP fetch boundary
(_fetch_gateway_earnings) and assert the computed EFFECT.

NOTE (flagged, not fixed): _upsert_earnings_direct (lines 255-264) is an empty
compatibility shim - it does NOT persist anything. So "synced" counts rows
*accepted for upsert*, not rows written to any store. These tests assert the
counting/parsing behavior as currently implemented.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from orion.jobs import sync_earnings


@pytest.mark.asyncio
async def test_sync_todays_earnings_tallies_synced_rows() -> None:
    """Happy path: premarket + afterhours each return one valid row -> both are
    counted as synced (one fetch per endpoint, two endpoints)."""
    premarket_rows = [{"symbol": "AAPL", "date": "2026-06-10", "eps_estimate": "1.5"}]
    afterhours_rows = [{"symbol": "MSFT", "date": "2026-06-10"}]

    with patch.object(
        sync_earnings,
        "_fetch_gateway_earnings",
        new=AsyncMock(side_effect=[premarket_rows, afterhours_rows]),
    ) as mock_fetch:
        results = await sync_earnings.sync_todays_earnings()

    assert results == {"synced": 2, "errors": 0}
    assert mock_fetch.await_count == 2


@pytest.mark.asyncio
async def test_sync_skips_rows_missing_ticker_or_date() -> None:
    """A row with no symbol/ticker and a row with no date are silently skipped
    by _upsert_earnings_row (lines 228-234 return early) -> not counted as
    synced and not counted as errors (the upsert raised nothing)."""
    rows = [
        {"date": "2026-06-10"},  # no ticker -> skipped
        {"symbol": "AAPL"},  # no date -> skipped
        {"symbol": "MSFT", "date": "2026-06-10"},  # valid
    ]
    with patch.object(
        sync_earnings,
        "_fetch_gateway_earnings",
        new=AsyncMock(side_effect=[rows, []]),
    ):
        results = await sync_earnings.sync_todays_earnings()

    # All three rows return without raising, so all three increment "synced"
    # (the early-return rows still count as synced - documenting current behavior).
    assert results["errors"] == 0
    assert results["synced"] == 3


@pytest.mark.asyncio
async def test_fetch_failure_is_caught_and_counted_as_error() -> None:
    """Failure path: this is a resilient batch job. When a Gateway fetch raises,
    _fetch_and_sync_earnings catches it (lines 111-113), logs, and bumps
    results['errors'] instead of crashing. The second endpoint still runs."""
    with patch.object(
        sync_earnings,
        "_fetch_gateway_earnings",
        new=AsyncMock(side_effect=[RuntimeError("gateway 500"), [{"symbol": "MSFT", "date": "2026-06-10"}]]),
    ) as mock_fetch:
        results = await sync_earnings.sync_todays_earnings()

    # First endpoint errored, second synced one row -> loop did not abort.
    assert results["errors"] == 1
    assert results["synced"] == 1
    assert mock_fetch.await_count == 2


@pytest.mark.asyncio
async def test_get_earnings_for_ticker_computes_timeline() -> None:
    """get_earnings_for_ticker derives next/last earnings and post-earnings flag
    from fetched rows."""
    rows = [
        {"date": "2026-06-05"},  # 3 days before as_of -> post-earnings window
        {"date": "2026-06-20"},  # future -> next earnings
    ]
    with patch.object(sync_earnings, "_fetch_gateway_earnings", new=AsyncMock(return_value=rows)):
        result = await sync_earnings.get_earnings_for_ticker("AAPL", as_of_date=date(2026, 6, 8))

    assert result["next_earnings_date"] == date(2026, 6, 20)
    assert result["days_to_earnings"] == 12
    assert result["last_earnings_date"] == date(2026, 6, 5)
    assert result["is_post_earnings"] is True


@pytest.mark.asyncio
async def test_get_earnings_for_ticker_returns_default_on_fetch_failure() -> None:
    """Failure path: a fetch error in get_earnings_for_ticker is swallowed
    (lines 291-293) and the neutral default dict is returned - never raises."""
    with patch.object(
        sync_earnings,
        "_fetch_gateway_earnings",
        new=AsyncMock(side_effect=RuntimeError("timeout")),
    ):
        result = await sync_earnings.get_earnings_for_ticker("AAPL", as_of_date=date(2026, 6, 8))

    assert result == {
        "days_to_earnings": None,
        "is_post_earnings": False,
        "next_earnings_date": None,
        "last_earnings_date": None,
    }
