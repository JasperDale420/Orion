from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import orion.clients.gateway_trading_client as gateway_module
from orion.clients.gateway_trading_client import GatewayTradingClient, GatewayTradingClientError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method_name,response,expected_message,event_type",
    [
        (
            "get_positions",
            {"error": "gateway unavailable"},
            "Gateway positions request failed",
            "GATEWAY_POSITIONS_REQUEST_FAILED",
        ),
        (
            "get_positions",
            {"positions": []},
            "Gateway positions response was not a list",
            "GATEWAY_POSITIONS_MALFORMED_RESPONSE",
        ),
        (
            "get_orders",
            {"error": "gateway unavailable"},
            "Gateway orders request failed",
            "GATEWAY_ORDERS_REQUEST_FAILED",
        ),
        (
            "get_orders",
            {"orders": []},
            "Gateway orders response was not a list",
            "GATEWAY_ORDERS_MALFORMED_RESPONSE",
        ),
    ],
)
async def test_gateway_client_raises_on_invalid_list_payloads(
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    response: dict[str, object],
    expected_message: str,
    event_type: str,
) -> None:
    client = GatewayTradingClient(base_url="http://gateway", api_key="test")
    monkeypatch.setattr(client, "_request", AsyncMock(return_value=response))

    mock_logger = MagicMock()
    monkeypatch.setattr(gateway_module, "logger", mock_logger)

    with pytest.raises(GatewayTradingClientError, match=expected_message):
        await getattr(client, method_name)()

    mock_logger.error.assert_called_once()
    assert mock_logger.error.call_args.kwargs["event_type"] == event_type


@pytest.mark.asyncio
async def test_gateway_client_returns_list_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    client = GatewayTradingClient(base_url="http://gateway", api_key="test")
    positions = [{"symbol": "AAPL"}]
    orders = [{"id": "order-1"}]
    mock_request = AsyncMock(side_effect=[positions, orders])
    monkeypatch.setattr(client, "_request", mock_request)

    assert await client.get_positions() == positions
    assert await client.get_orders() == orders
