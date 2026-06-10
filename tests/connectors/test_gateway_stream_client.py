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

    # Current Gateway WS pushes bars as bare EventEnvelopes — no top-level
    # `type`, only `feed` and envelope fields. Regression for the silent-drop
    # that left bronze_events without a single ALPACA_BAR_1M for 60+ hours.
    bare_envelope = {
        "event_id": "abc",
        "provider": "alpaca",
        "feed": "bars",
        "source": "websocket",
        "instrument_type": "equity",
        "instrument_key": "equity:AAPL",
        "symbol": "AAPL",
        "ts_event": "2026-05-08T13:30:00+00:00",
        "payload": {"o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 100, "t": "2026-05-08T13:30:00+00:00"},
    }
    assert GatewayStreamClient._is_bar_message(bare_envelope) is True

    # subscription_ack uses `feeds` (plural) and must NOT match.
    assert (
        GatewayStreamClient._is_bar_message(
            {"type": "subscription_ack", "status": "ok", "feeds": ["bars"], "subscribed": ["AAPL"]}
        )
        is False
    )


@pytest.mark.asyncio
async def test_restart_resets_running_and_reconnects(monkeypatch) -> None:
    """After reconnect exhaustion leaves the client dead, restart() revives it."""
    client = GatewayStreamClient(gateway_url="http://localhost:8000", api_key="test-key")
    # Simulate the post-exhaustion state: stopped, no receive task.
    client._running = False
    client._receive_task = None
    client._subscribed_symbols = {"AAPL"}

    sent: list[list[str]] = []

    async def fake_send_subscribe(symbols: list[str]) -> bool:
        sent.append(list(symbols))
        return True

    async def fake_reconnect() -> bool:
        # The real _reconnect_with_backoff resubscribes stored symbols on a
        # successful connect; mirror that so the test exercises the single
        # subscribe path (restart() must NOT resubscribe a second time).
        if client._subscribed_symbols:
            await client._send_subscribe(list(client._subscribed_symbols))
        return True

    async def fake_receive_loop() -> None:
        return None

    monkeypatch.setattr(client, "_reconnect_with_backoff", fake_reconnect)
    monkeypatch.setattr(client, "_send_subscribe", fake_send_subscribe)
    monkeypatch.setattr(client, "_receive_loop", fake_receive_loop)

    result = await client.restart()

    assert result is True
    assert client.is_running is True
    # Subscribed exactly once — no double-subscribe from restart().
    assert sent == [["AAPL"]]
    assert client._receive_task is not None
    # Cleanup the spawned task.
    await client.stop()


@pytest.mark.asyncio
async def test_restart_returns_false_when_reconnect_fails(monkeypatch) -> None:
    client = GatewayStreamClient(gateway_url="http://localhost:8000", api_key="test-key")
    client._running = False

    async def fake_reconnect() -> bool:
        return False

    monkeypatch.setattr(client, "_reconnect_with_backoff", fake_reconnect)

    result = await client.restart()

    assert result is False
    assert client.is_running is False
