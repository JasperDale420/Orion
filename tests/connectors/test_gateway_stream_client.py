from __future__ import annotations

from datetime import UTC, datetime

import pytest

from orion.connectors.gateway_stream_client import GatewayStreamClient


@pytest.mark.asyncio
async def test_processes_gateway_data_envelope_bar_message() -> None:
    client = GatewayStreamClient(gateway_url="http://localhost:8000", api_key="test-key")

    msg = {
        "type": "data",
        "feed": "bars",
        "symbol": "AAPL",
        "event_id": "evt-123",
        "envelope": {
            "event_id": "evt-123",
            "instrument_key": "equity:AAPL",
            "ts_event": "2026-02-05T14:31:00Z",
            "payload": {
                "T": "b",
                "S": "AAPL",
                "t": "2026-02-05T14:31:00Z",
                "o": 190.0,
                "h": 191.0,
                "l": 189.0,
                "c": 190.5,
                "v": 1200,
            },
        },
        "data": {
            "T": "b",
            "S": "AAPL",
            "t": "2026-02-05T14:31:00Z",
            "o": 190.0,
            "h": 191.0,
            "l": 189.0,
            "c": 190.5,
            "v": 1200,
        },
    }

    await client._process_bar_message(msg)

    events = client.drain_events()
    assert len(events) == 1
    event = events[0]

    assert event.event_id == "evt-123"
    assert event.event_type == "ALPACA_BAR_1M"
    assert event.source == "ALPACA"
    assert event.payload["symbol"] == "AAPL"
    assert event.payload["ticker"] == "AAPL"
    assert event.payload["instrument_key"] == "equity:AAPL"
    assert event.event_ts_utc == datetime(2026, 2, 5, 14, 31, tzinfo=UTC)


@pytest.mark.asyncio
async def test_rejects_invalid_close_price() -> None:
    client = GatewayStreamClient(gateway_url="http://localhost:8000", api_key="test-key")

    msg = {
        "type": "data",
        "feed": "bars",
        "symbol": "AAPL",
        "data": {
            "T": "b",
            "S": "AAPL",
            "t": "2026-02-05T14:31:00Z",
            "o": 190.0,
            "h": 191.0,
            "l": 189.0,
            "c": 0,
            "v": 1200,
        },
    }

    await client._process_bar_message(msg)

    assert client.drain_events() == []


@pytest.mark.asyncio
async def test_subscribe_before_connection_is_queued() -> None:
    client = GatewayStreamClient(gateway_url="http://localhost:8000", api_key="test-key")

    await client.subscribe(["AAPL", "MSFT"])

    assert client.subscribed_symbols == {"AAPL", "MSFT"}


def test_bar_message_detection_handles_gateway_shape() -> None:
    assert GatewayStreamClient._is_bar_message({"type": "data", "feed": "bars"}) is True
    assert GatewayStreamClient._is_bar_message({"type": "ALPACA_BAR_1M"}) is True
    assert GatewayStreamClient._is_bar_message({"type": "data", "feed": "quotes"}) is False
