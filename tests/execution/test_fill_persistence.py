from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from tenacity import RetryError

from orion.execution import persistence
from orion.execution.persistence import (
    _coerce_timestamp,
    is_fill_processed,
    mark_fill_processed,
    persist_fill_record,
)
from orion.storage.db import async_session_factory
from orion.storage.models_execution import FillRecord


@pytest.mark.asyncio
async def test_persist_fill_record_updates_existing_order_row() -> None:
    fill_one = {
        "id": "broker-2",
        "symbol": "AAPL",
        "client_order_id": "orion_456",
        "filled_qty": 4.0,
        "qty": 10.0,
        "filled_avg_price": 2.5,
        "side": "buy",
    }
    fill_two = {
        "id": "broker-2",
        "symbol": "AAPL",
        "client_order_id": "orion_456",
        "filled_qty": 10.0,
        "qty": 10.0,
        "filled_avg_price": 2.6,
        "side": "buy",
    }

    await persist_fill_record(fill_one)
    await persist_fill_record(fill_two)

    async with async_session_factory() as session:
        row = (
            (await session.execute(select(FillRecord).where(FillRecord.broker_order_id == "broker-2")))
            .scalars()
            .first()
        )

    assert row is not None
    assert row.filled_qty == 10.0
    assert row.filled_avg_price == 2.6


@pytest.mark.unit
def test_coerce_timestamp_handles_iso_string() -> None:
    """Gateway-returned `filled_at` is an ISO-8601 string — coerce to a
    tz-aware datetime so persist_fill_record doesn't silently fail against
    the TIMESTAMP WITH TIME ZONE column.
    """
    dt = _coerce_timestamp("2026-05-14T15:00:53.956207Z")
    assert isinstance(dt, datetime)
    assert dt.tzinfo is not None
    assert dt.year == 2026
    assert dt.month == 5
    assert dt.day == 14


@pytest.mark.unit
def test_coerce_timestamp_passthrough_datetime() -> None:
    """An existing datetime is returned unchanged (with UTC backfill when naive)."""
    aware = datetime(2026, 5, 14, 15, 0, 0, tzinfo=UTC)
    assert _coerce_timestamp(aware) is aware

    naive = datetime(2026, 5, 14, 15, 0, 0)
    coerced = _coerce_timestamp(naive)
    assert coerced is not None
    assert coerced.tzinfo is not None


@pytest.mark.unit
def test_coerce_timestamp_none_and_garbage() -> None:
    assert _coerce_timestamp(None) is None
    assert _coerce_timestamp("") is None
    assert _coerce_timestamp("not-a-date") is None


@pytest.mark.asyncio
async def test_persist_fill_record_with_string_timestamp() -> None:
    """Regression: Gateway sends ISO-8601 string for `filled_at`. The earlier
    silent failure left orphaned ProcessedFill markers — this test guards
    against re-introducing the bug.
    """
    fill = {
        "id": "broker-strts-1",
        "symbol": "MSFT",
        "client_order_id": "orion_strts",
        "filled_qty": 3.0,
        "qty": 3.0,
        "filled_avg_price": 1.25,
        "side": "buy",
        "filled_at": "2026-05-14T15:00:53.956207Z",
    }

    await persist_fill_record(fill)

    async with async_session_factory() as session:
        row = (
            (await session.execute(select(FillRecord).where(FillRecord.broker_order_id == "broker-strts-1")))
            .scalars()
            .first()
        )

    # Roundtrip via SQLite drops tz info — the load-bearing assertion is that
    # the row was written at all (vs. the silent-failure pre-fix where the
    # asyncpg DataError swallowed the insert).
    assert row is not None
    assert row.filled_qty == 3.0
    assert row.filled_at_utc is not None
    assert row.filled_at_utc.year == 2026
    assert row.filled_at_utc.month == 5
    assert row.filled_at_utc.day == 14


@pytest.mark.asyncio
async def test_fill_idempotency_lookup_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(persistence, "db_query", AsyncMock(side_effect=RuntimeError("database unavailable")))

    with pytest.raises(RuntimeError, match="database unavailable"):
        await is_fill_processed("broker-1:1")


@pytest.mark.asyncio
async def test_fill_marker_write_failure_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(persistence, "db_write", AsyncMock(side_effect=RuntimeError("database unavailable")))

    with pytest.raises(RetryError) as caught:
        await mark_fill_processed("broker-1:1")
    assert isinstance(caught.value.last_attempt.exception(), RuntimeError)


@pytest.mark.asyncio
async def test_fill_record_write_failure_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(persistence, "db_write", AsyncMock(side_effect=RuntimeError("database unavailable")))

    with pytest.raises(RetryError) as caught:
        await persist_fill_record({"id": "broker-1", "symbol": "SPY", "filled_qty": 1, "filled_avg_price": 2})
    assert isinstance(caught.value.last_attempt.exception(), RuntimeError)
