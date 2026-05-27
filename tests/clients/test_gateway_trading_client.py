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


@pytest.mark.asyncio
async def test_create_order_sends_all_fields_as_query_params_not_body() -> None:
    """Regression for 2026-05-22 incident (fix commit 51699c8): the
    Gateway's ``POST /api/v1/alpaca/orders`` endpoint declares EVERY
    order field as a bare-typed FastAPI parameter (no ``Body`` marker),
    so FastAPI classifies them all as query params. Before the fix,
    Orion's client sent them in the JSON body, and Gateway returned
    422 with ``loc:["query","symbol"] / msg:"Field required"`` on
    EVERY live order submission. 174 orders rejected, 0 fills, full
    morning of trading lost.

    The fix is one line — switch ``json_body=`` to ``params=`` — but
    the bug pattern is invisible to type-checkers and was caught only
    by production failure. This test pins the transport shape so a
    future refactor can't silently revert: assert that ``symbol`` and
    ``side`` travel via ``params=`` (query string), NOT via ``json=``
    (request body).

    See `tests/contracts/test_create_order_contract.py` for the
    cross-repo contract test that validates this against the Gateway's
    actual FastAPI signature.
    """
    client = GatewayTradingClient(base_url="http://gateway", api_key="test")

    # Capture the kwargs of the underlying httpx request without
    # actually executing one. AsyncMock returns a coroutine; the
    # mock_response we configure must be JSON-decodable and have
    # raise_for_status().
    mock_response = MagicMock()
    mock_response.json = MagicMock(return_value={"data": {"id": "order-1", "status": "accepted"}})
    mock_response.raise_for_status = MagicMock(return_value=None)

    mock_httpx = MagicMock()
    mock_httpx.request = AsyncMock(return_value=mock_response)
    mock_httpx.is_closed = False

    # Inject our captured-kwarg mock as the underlying httpx client.
    client._client = mock_httpx

    await client.create_order(
        symbol="AAPL260529P00315000",
        qty=10,
        side="sell",
        order_type="limit",
        limit_price=5.40,
        client_order_id="orion_test_query_shape",
    )

    assert mock_httpx.request.call_count == 1
    call_kwargs = mock_httpx.request.call_args.kwargs
    method = mock_httpx.request.call_args.args[0]
    path = mock_httpx.request.call_args.args[1]

    assert method == "POST"
    assert path == "/api/v1/alpaca/orders"

    params = call_kwargs.get("params") or {}
    json_body = call_kwargs.get("json")

    # The exact 2026-05-22 footgun. If `symbol` or `side` ever migrates
    # back into the body, the Gateway returns 422 and we re-bleed.
    assert params.get("symbol") == "AAPL260529P00315000", (
        f"`symbol` MUST travel in `params=` (query string), not the body. "
        f"Gateway declares it as a FastAPI Query parameter and returns 422 "
        f"otherwise — see incident 2026-05-22. Got params={params}, json={json_body}."
    )
    assert params.get("side") == "sell", (
        f"`side` MUST travel in `params=` (query string), not the body. Got params={params}, json={json_body}."
    )
    # The rest of the fields share the same Gateway-side classification —
    # all bare-typed → query. Verify they're all present in params.
    for field in ("qty", "order_type", "time_in_force", "limit_price", "client_order_id"):
        assert field in params, f"`{field}` MUST be in `params=`; got params keys={list(params)}"

    # And nothing should be in the JSON body — Gateway expects POST with no
    # body for orders. A non-None json= would either be ignored (best case)
    # or interpreted as a body Gateway doesn't expect (worst case).
    assert json_body is None, (
        f"`POST /alpaca/orders` accepts only query params; the client must send no JSON body. Got json={json_body}."
    )
