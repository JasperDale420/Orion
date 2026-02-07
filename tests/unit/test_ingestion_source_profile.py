from __future__ import annotations

import pytest

from orion.ingestion.service import IngestionService


class _DummyAlpacaMarketConnector:
    def __init__(self, *args, **kwargs) -> None:
        return None


class _DummyAlpacaStreamConnector:
    def __init__(self, *args, **kwargs) -> None:
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
