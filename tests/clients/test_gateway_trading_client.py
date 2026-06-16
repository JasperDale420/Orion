from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import orion.clients.gateway_trading_client as gateway_module
from orion.clients.gateway_trading_client import GatewayTradingClient, GatewayTradingClientError


@pytest.mark.asyncio
async def test_request_propagates_alpaca_error_body_on_http_error() -> None:
    """On an HTTP error (e.g. 403), ``_request`` must surface the response
    BODY, not just the bare status line. The Gateway proxies Alpaca's error
    verbatim, so the body carries the real reason code — 40310000
    (insufficient day-trading buying power), 42210000 (position-intent
    mismatch), "potential wash trade detected", etc. Without this, the
    ``orders`` table only ever recorded "403 Forbidden" and the close-path
    intent-retry has nothing to branch on (RCA 2026-06-05)."""
    client = GatewayTradingClient(base_url="http://gateway", api_key="test")

    body = (
        '{"detail":"Alpaca API Error: {\\"code\\":40310000,\\"message\\":\\"insufficient day trading buying power\\"}"}'
    )
    request = httpx.Request("POST", "http://gateway/api/v1/alpaca/orders")
    response = httpx.Response(403, text=body, request=request)

    mock_httpx = MagicMock()
    mock_httpx.request = AsyncMock(return_value=response)
    mock_httpx.is_closed = False
    client._client = mock_httpx

    result = await client.create_order(
        symbol="MU260612P00790000",
        qty=1,
        side="buy",
        order_type="limit",
        limit_price=4.4,
        client_order_id="orion_test_body",
    )

    assert "error" in result
    assert result.get("status_code") == 403
    # The actual Alpaca reason code + message must survive to the caller.
    assert "40310000" in str(result.get("detail"))
    assert "insufficient day trading buying power" in str(result.get("detail"))


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


@pytest.mark.asyncio
async def test_get_option_quote_extracts_contract_bid_ask() -> None:
    """`get_option_quote` parses the underlying from the OCC symbol, fetches the
    chain, and returns the matching contract's bid/ask/last (RCA 2026-06-08:
    used to price an options close at the live market, not a stale tracked mark)."""
    client = GatewayTradingClient(base_url="http://gateway", api_key="test")
    client.get_option_chain = AsyncMock(
        return_value={
            "underlying": "MU",
            "contracts": [
                {"contract_symbol": "MU260612P00780000", "bid": "1.0", "ask": "1.2"},
                {"contract_symbol": "MU260612P00790000", "bid": "6.4", "ask": "6.6", "last": "6.45"},
            ],
        }
    )

    q = await client.get_option_quote("MU260612P00790000")

    assert q == {"bid": 6.4, "ask": 6.6, "last": 6.45, "timestamp": None}
    client.get_option_chain.assert_awaited_once_with("MU")


@pytest.mark.asyncio
async def test_get_option_quote_missing_contract_returns_empty() -> None:
    """A contract not present in the chain → empty dict (caller falls back)."""
    client = GatewayTradingClient(base_url="http://gateway", api_key="test")
    client.get_option_chain = AsyncMock(return_value={"underlying": "MU", "contracts": []})
    assert await client.get_option_quote("MU260612P00790000") == {}


@pytest.mark.asyncio
async def test_get_option_quote_bad_symbol_returns_empty() -> None:
    """An un-parseable (non-OCC) symbol → empty dict, no chain fetch."""
    client = GatewayTradingClient(base_url="http://gateway", api_key="test")
    client.get_option_chain = AsyncMock(return_value={})
    assert await client.get_option_quote("AAPL") == {}
    client.get_option_chain.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_option_quote_caches_chain_per_underlying() -> None:
    """A second lookup on the same underlying within the TTL reuses the cached
    chain (one fetch), so a close retry loop doesn't refetch the multi-MB chain."""
    client = GatewayTradingClient(base_url="http://gateway", api_key="test")
    client.get_option_chain = AsyncMock(
        return_value={
            "underlying": "MU",
            "contracts": [
                {"contract_symbol": "MU260612P00790000", "bid": "6.4", "ask": "6.6"},
                {"contract_symbol": "MU260612C00800000", "bid": "2.1", "ask": "2.3"},
            ],
        }
    )
    a = await client.get_option_quote("MU260612P00790000")
    b = await client.get_option_quote("MU260612C00800000")
    assert a["bid"] == 6.4 and b["bid"] == 2.1
    client.get_option_chain.assert_awaited_once()  # one fetch served both


@pytest.mark.asyncio
async def test_get_orders_forwards_submitted_at_window_params() -> None:
    """``get_orders`` serializes the additive after/until/nested params other
    callers depend on (tz-aware ISO-8601, nested flag)."""
    from datetime import UTC, datetime

    client = GatewayTradingClient(base_url="http://gateway", api_key="test")
    captured: dict[str, object] = {}

    async def fake_request(method, path, *, params=None, json_body=None):
        captured["method"] = method
        captured["path"] = path
        captured["params"] = params
        return []

    client._request = fake_request  # type: ignore[assignment]

    await client.get_orders(
        status="all",
        limit=500,
        direction="asc",
        after=datetime(2026, 3, 13, tzinfo=UTC),
        until=datetime(2026, 6, 12, tzinfo=UTC),
        nested=True,
    )
    params = captured["params"]
    assert params["status"] == "all"
    assert params["direction"] == "asc"
    assert params["after"] == "2026-03-13T00:00:00+00:00"
    assert params["until"] == "2026-06-12T00:00:00+00:00"
    assert params["nested"] is True


@pytest.mark.asyncio
async def test_get_orders_strips_gateway_ownership_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gateway per-client isolation wraps Orion's ``orion_<uuid>`` ids as
    ``c-orion-orion_<uuid>`` on broker orders. ``get_orders`` must strip that
    wrapper (incl. nested bracket legs) so Orion's ``orion_``-prefix attribution
    (fill_processor, position_monitor, reconcile_pnl) keeps matching. Legacy
    un-wrapped ids pass through untouched."""
    from orion.execution.attribution import is_orion_owned

    client = GatewayTradingClient(base_url="http://gateway", api_key="test")
    broker_orders = [
        {
            "id": "brk-1",
            "client_order_id": "c-orion-orion_abc123",
            "legs": [{"id": "leg-1", "client_order_id": "c-orion-orion_abc123-tp"}],
        },
        {"id": "brk-2", "client_order_id": "orion_legacy_unwrapped"},  # pre-prefix order
        {"id": "brk-3", "client_order_id": None},  # missing coid tolerated
    ]
    monkeypatch.setattr(client, "_request", AsyncMock(return_value=broker_orders))

    orders = await client.get_orders(status="all")

    assert orders[0]["client_order_id"] == "orion_abc123"
    assert orders[0]["legs"][0]["client_order_id"] == "orion_abc123-tp"
    assert orders[1]["client_order_id"] == "orion_legacy_unwrapped"
    assert orders[2]["client_order_id"] is None
    # The whole point: stripped ids attribute back to Orion.
    assert is_orion_owned(orders[0]["client_order_id"])
    assert is_orion_owned(orders[1]["client_order_id"])


@pytest.mark.asyncio
async def test_get_order_strips_gateway_ownership_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Single-order lookup applies the same ownership-prefix normalization."""
    client = GatewayTradingClient(base_url="http://gateway", api_key="test")
    monkeypatch.setattr(
        client,
        "_request",
        AsyncMock(return_value={"id": "brk-9", "client_order_id": "c-orion-orion_xyz"}),
    )
    order = await client.get_order("brk-9")
    assert order["client_order_id"] == "orion_xyz"
