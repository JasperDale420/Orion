from __future__ import annotations

import pytest

from orion.connectors.gateway_stream_client import GatewayStreamClient


@pytest.mark.parametrize(
    ("gateway_url", "expected_ws_url"),
    [
        ("http://localhost:8080", "ws://localhost:8080/ws"),
        ("http://localhost:8080/api/v1", "ws://localhost:8080/ws"),
        ("https://gateway.example.com/api/v1", "wss://gateway.example.com/ws"),
        ("ws://localhost:8080", "ws://localhost:8080/ws"),
        ("ws://localhost:8080/api/v1", "ws://localhost:8080/ws"),
        ("wss://gateway.example.com/api/v1", "wss://gateway.example.com/ws"),
        ("localhost:8080", "ws://localhost:8080/ws"),
    ],
)
def test_gateway_stream_client_normalizes_ws_url_variants(gateway_url: str, expected_ws_url: str) -> None:
    client = GatewayStreamClient(gateway_url=gateway_url, api_key="k")
    assert client.ws_url == expected_ws_url


@pytest.mark.asyncio
async def test_connect_auth_failure_closes_and_resets_websocket(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeWebSocket:
        def __init__(self) -> None:
            self.closed = False
            self.sent = []

        async def send(self, payload: str) -> None:
            self.sent.append(payload)

        async def recv(self) -> str:
            return '{"status":"error","message":"bad key"}'

        async def close(self) -> None:
            self.closed = True

    fake_socket = _FakeWebSocket()

    async def _fake_connect(*args, **kwargs):
        return fake_socket

    monkeypatch.setattr("orion.connectors.gateway_stream_client.websockets.connect", _fake_connect)

    client = GatewayStreamClient(gateway_url="http://localhost:8080/api/v1", api_key="bad")
    ok = await client.connect()

    assert ok is False
    assert fake_socket.closed is True
    assert client._websocket is None
    assert client._authenticated is False
