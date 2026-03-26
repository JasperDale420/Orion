from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from orion.ingestion.service import IngestionService


class _DummyRollupJob:
    def __init__(self, *args, **kwargs) -> None:
        return None

    async def run_forever(self) -> None:
        return None


async def _async_noop(*args, **kwargs) -> None:
    return None


@pytest.mark.asyncio
async def test_ingestion_source_profile_polling_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Service reports gateway_stream data source with bar and flow event types."""
    mock_stream = MagicMock()
    mock_stream.is_running = False
    mock_stream.subscribed_symbols = set()
    monkeypatch.setattr("orion.ingestion.service.create_gateway_stream_client", lambda *a, **kw: mock_stream)
    service = IngestionService()
    profile = service._active_event_source_profile()

    assert profile["data_source"] == "gateway_stream+heber_flow"
    assert "ALPACA_BAR_1M" in profile["produced_event_types"]
    assert profile["flow_source"] == "heber_silver"


@pytest.mark.asyncio
async def test_initialize_skips_startup_earnings_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    sync_call_count = {"value": 0}

    async def _sync_todays_earnings() -> dict[str, int]:
        sync_call_count["value"] += 1
        return {"synced": 0, "errors": 0}

    mock_stream = MagicMock()
    mock_stream.is_running = False
    mock_stream.subscribed_symbols = set()
    mock_stream.start = AsyncMock()
    mock_stream.subscribe = AsyncMock()

    monkeypatch.setattr("orion.ingestion.service.create_gateway_stream_client", lambda *a, **kw: mock_stream)
    monkeypatch.setattr("orion.ingestion.service.init_db", _async_noop)
    monkeypatch.setattr("orion.jobs.sync_earnings.sync_todays_earnings", _sync_todays_earnings)
    monkeypatch.setattr("orion.jobs.rollup_job.RollupJob", _DummyRollupJob)

    service = IngestionService()
    monkeypatch.setattr(service.universe, "hydrate_from_db", _async_noop)
    monkeypatch.setattr(service.feature_engine, "hydrate_history", AsyncMock())

    await service.initialize()
    if service._rollup_task is not None:
        await asyncio.wait_for(service._rollup_task, timeout=1.0)

    assert sync_call_count["value"] == 0
