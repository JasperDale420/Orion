from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from orion.processing import persistence
from orion.storage.models import BronzeEvent


def _sample_flow_event() -> BronzeEvent:
    now = datetime.now(UTC)
    return BronzeEvent(
        event_id="evt_flow_gate",
        source="UW",
        event_type="UW_FLOW",
        ticker="AAPL",
        event_ts_utc=now,
        received_ts_utc=now,
        payload={
            "ticker": "AAPL",
            "flow_ts_utc": now.isoformat(),
            "put_call": "C",
            "expiry": "2026-03-20",
            "strike": 200.0,
            "price": 1.5,
            "size_contracts": 10,
            "premium_usd": 15000.0,
        },
        schema_version="v1",
    )


@pytest.mark.asyncio
async def test_persist_silver_from_bronze_skips_local_writes_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ORION_ENABLE_LOCAL_SILVER_PERSISTENCE", raising=False)
    session = AsyncMock()

    await persistence.persist_silver_from_bronze(session, [_sample_flow_event()])

    assert session.execute.await_count == 0


@pytest.mark.asyncio
async def test_persist_silver_from_bronze_remains_noop_even_if_legacy_gate_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORION_ENABLE_LOCAL_SILVER_PERSISTENCE", "true")
    session = AsyncMock()

    await persistence.persist_silver_from_bronze(session, [_sample_flow_event()])

    assert session.execute.await_count == 0
