from __future__ import annotations

import asyncio

import pytest

from orion.ingestion.service import IngestionService


class _DummyAlpacaMarketConnector:
    def __init__(self, *args, **kwargs) -> None:
        return None


class _DummyAlpacaStreamConnector:
    def __init__(self, *args, **kwargs) -> None:
        return None


class _DummyRollupJob:
    def __init__(self, *args, **kwargs) -> None:
        return None

    async def run_forever(self) -> None:
        return None


async def _async_noop(*args, **kwargs) -> None:
    return None


@pytest.mark.asyncio
async def test_ingestion_source_profile_polling_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORION_USE_ALPACA_STREAMING", "false")
    monkeypatch.setattr("orion.ingestion.service.AlpacaMarketConnector", _DummyAlpacaMarketConnector)
    monkeypatch.setattr("orion.ingestion.service.AlpacaStreamConnector", _DummyAlpacaStreamConnector)

    service = IngestionService()
    profile = service._active_event_source_profile()

    assert profile["alpaca_streaming_enabled"] is False
    assert profile["alpaca_mode"] == "polling"
    assert profile["produced_event_types"] == ["ALPACA_BAR_1M"]
    assert profile["uw_flow_darkpool_ingestion"] == "external_gateway_heber_pipeline"


@pytest.mark.asyncio
async def test_initialize_skips_startup_earnings_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    sync_call_count = {"value": 0}

    async def _sync_todays_earnings() -> dict[str, int]:
        sync_call_count["value"] += 1
        return {"synced": 0, "errors": 0}

    monkeypatch.setenv("ORION_USE_ALPACA_STREAMING", "false")
    monkeypatch.setattr("orion.ingestion.service.AlpacaMarketConnector", _DummyAlpacaMarketConnector)
    monkeypatch.setattr("orion.ingestion.service.AlpacaStreamConnector", _DummyAlpacaStreamConnector)
    monkeypatch.setattr("orion.ingestion.service.init_db", _async_noop)
    monkeypatch.setattr("orion.jobs.sync_earnings.sync_todays_earnings", _sync_todays_earnings)
    monkeypatch.setattr("orion.jobs.rollup_job.RollupJob", _DummyRollupJob)

    service = IngestionService()
    monkeypatch.setattr(service.universe, "hydrate_from_db", _async_noop)

    await service.initialize()
    if service._rollup_task is not None:
        await asyncio.wait_for(service._rollup_task, timeout=1.0)

    assert sync_call_count["value"] == 0
