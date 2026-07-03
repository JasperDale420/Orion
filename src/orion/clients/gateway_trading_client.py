"""
Data Gateway Trading Client.

Thin async httpx wrapper around the Data Gateway's Alpaca trading endpoints.
Routes all order management, position tracking, and account queries through
the centralized Data Gateway REST API.
"""

from datetime import datetime
from typing import Any

import httpx
import structlog

from orion.config import system_settings

logger = structlog.get_logger("orion.clients.gateway_trading")

_RESPONSE_DATA_KEY = "data"

# Data-Gateway per-client order ownership isolation (gateway PR
# fix/per-client-order-isolation-v2) transparently wraps every effective
# ``client_order_id`` sent to the shared Alpaca account with a
# ``c-{gateway_client_id}-`` prefix, and returns broker orders carrying that
# wrapped value. Orion authenticates as gateway client ``orion``, so its
# minted ``orion_<uuid>`` ids come back as ``c-orion-orion_<uuid>``.
#
# Orion's attribution layer (orion.execution.attribution) keys off the bare
# ``orion_`` prefix and OrderRecord rows store the minted (un-wrapped) id, so
# we strip the gateway wrapper at this boundary. Everything downstream
# (fill_processor, position_monitor, reconcile_pnl, the orphan/backfill
# scripts) then keeps operating in Orion's own ``orion_`` namespace.
# Values without the wrapper (legacy pre-prefix orders) pass through unchanged.
_GATEWAY_OWNERSHIP_PREFIX = "c-orion-"


def _strip_ownership_prefix(client_order_id: Any) -> Any:
    """Strip the gateway ``c-orion-`` ownership wrapper from a client_order_id.

    Returns the value unchanged if it is missing the wrapper or not a string.
    """
    if isinstance(client_order_id, str) and client_order_id.startswith(_GATEWAY_OWNERSHIP_PREFIX):
        return client_order_id[len(_GATEWAY_OWNERSHIP_PREFIX) :]
    return client_order_id


def _normalize_order_attribution(order: Any) -> Any:
    """Normalize the ``client_order_id`` on a broker order (and nested legs).

    Mutates and returns the order dict so all downstream attribution sees the
    bare ``orion_`` id rather than the gateway-wrapped ``c-orion-orion_`` form.
    """
    if not isinstance(order, dict):
        return order
    if "client_order_id" in order:
        order["client_order_id"] = _strip_ownership_prefix(order["client_order_id"])
    legs = order.get("legs")
    if isinstance(legs, list):
        for leg in legs:
            _normalize_order_attribution(leg)
    return order


class GatewayTradingClientError(RuntimeError):
    """Raised when the Gateway returns an error payload or invalid shape."""


class GatewayTradingClient:
    """
    Async client for Data Gateway trading endpoints.

    All Alpaca brokerage operations go through ``/api/v1/alpaca/*``.
    Auth is via ``X-Gateway-Key`` header; the Gateway routes to Alpaca
    paper or live based on its own configuration.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or system_settings.data_gateway_url).rstrip("/")
        self.api_key = api_key or system_settings.data_gateway_api_key or ""
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        # Short-TTL cache of option chains keyed by underlying, so a burst of
        # close attempts (multiple contracts on the same name, or a retry loop)
        # doesn't refetch the multi-MB chain each time.
        self._chain_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._chain_cache_ttl_s = 5.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {}
            if self.api_key:
                headers["X-Gateway-Key"] = self.api_key
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute an HTTP request and return the parsed response."""
        client = await self._get_client()
        try:
            response = await client.request(
                method,
                path,
                params=params,
                json=json_body,
            )
            response.raise_for_status()
            body = response.json()
            # Gateway wraps responses in {"success": true, "data": ...}
            if isinstance(body, dict) and _RESPONSE_DATA_KEY in body:
                return body[_RESPONSE_DATA_KEY]
            return body
        except httpx.HTTPStatusError as exc:
            # Surface the response BODY, not just the bare status line.
            # The Gateway proxies Alpaca's error verbatim, so the body
            # carries the actual reason code (40310000 insufficient
            # day-trading buying power, 42210000 position-intent mismatch,
            # "potential wash trade detected", …). `str(exc)` is only
            # "Client error '403 Forbidden' for url …" — useless for
            # operators and for the close-path intent retry. Callers that
            # persist `error_message` or branch on the reason read `detail`.
            body = exc.response.text
            logger.error(
                "gateway_trading_http_error",
                method=method,
                path=path,
                status=exc.response.status_code,
                detail=body[:500],
            )
            return {"error": str(exc), "detail": body, "status_code": exc.response.status_code}
        except Exception as exc:
            logger.error("gateway_trading_error", method=method, path=path, error=str(exc))
            return {"error": str(exc)}

    # ── Account ──────────────────────────────────────────────

    async def get_account(self) -> dict[str, Any]:
        """Get Alpaca account information (equity, buying power, etc.)."""
        return await self._request("GET", "/api/v1/alpaca/account")

    # ── Positions ────────────────────────────────────────────

    async def get_positions(self) -> list[dict[str, Any]]:
        """Get all open positions."""
        result = await self._request("GET", "/api/v1/alpaca/positions")
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "error" in result:
            logger.error(
                "gateway_trading_positions_request_failed",
                event_type="GATEWAY_POSITIONS_REQUEST_FAILED",
                error=result["error"],
            )
            raise GatewayTradingClientError(f"Gateway positions request failed: {result['error']}")

        logger.error(
            "gateway_trading_positions_malformed_response",
            event_type="GATEWAY_POSITIONS_MALFORMED_RESPONSE",
            response_type=type(result).__name__,
            response_preview=repr(result)[:500],
        )
        raise GatewayTradingClientError("Gateway positions response was not a list")

    async def get_position(self, symbol: str) -> dict[str, Any]:
        """Get position for a specific symbol."""
        return await self._request("GET", f"/api/v1/alpaca/positions/{symbol}")

    async def close_position(self, symbol: str, qty: float | None = None) -> dict[str, Any]:
        """Close a position (full or partial)."""
        params = {}
        if qty is not None:
            params["qty"] = str(qty)
        return await self._request("DELETE", f"/api/v1/alpaca/positions/{symbol}", params=params or None)

    # ── Orders ───────────────────────────────────────────────

    async def create_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "limit",
        time_in_force: str = "day",
        limit_price: float | None = None,
        client_order_id: str | None = None,
        position_intent: str | None = None,
    ) -> dict[str, Any]:
        """Submit a new order through the Gateway.

        2026-05-22: Gateway's POST /api/v1/alpaca/orders signature declares
        all fields as FastAPI Query parameters (no Body annotation), so they
        must travel in the URL query string, not the JSON body. Sending them
        as a JSON body returns 422 with
            loc: ["query", "symbol"] / ["query", "side"]
        — see incident write-up in scripts/close_orphaned_positions.py
        (orphan-close, 5/22). This bug silently blocked 100% of Orion's
        live order submissions until it was caught running the orphan-close
        the morning after the launchd job mis-fired.
        """
        params: dict[str, Any] = {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "order_type": order_type,
            "time_in_force": time_in_force,
        }
        if limit_price is not None:
            params["limit_price"] = limit_price
        if client_order_id is not None:
            params["client_order_id"] = client_order_id
        if position_intent is not None:
            params["position_intent"] = position_intent
        return await self._request("POST", "/api/v1/alpaca/orders", params=params)

    async def get_orders(
        self,
        status: str = "open",
        limit: int = 50,
        direction: str = "desc",
        *,
        after: datetime | None = None,
        until: datetime | None = None,
        nested: bool = False,
    ) -> list[dict[str, Any]]:
        """List orders with optional status + submitted_at date-window filter.

        ``after``/``until`` filter by SUBMITTED_AT (Alpaca behaviour — NOT
        filled_at), tz-aware datetimes serialized as ISO-8601. ``nested=True``
        nests bracket child fills under each parent's ``legs``. ``direction``
        is "asc" (oldest-first) or "desc" (newest-first).
        """
        params: dict[str, Any] = {"status": status, "limit": limit, "direction": direction}
        if after is not None:
            params["after"] = after.isoformat()
        if until is not None:
            params["until"] = until.isoformat()
        if nested:
            params["nested"] = True
        result = await self._request(
            "GET",
            "/api/v1/alpaca/orders",
            params=params,
        )
        if isinstance(result, list):
            return [_normalize_order_attribution(o) for o in result]
        if isinstance(result, dict) and "error" in result:
            logger.error(
                "gateway_trading_orders_request_failed",
                event_type="GATEWAY_ORDERS_REQUEST_FAILED",
                error=result["error"],
            )
            raise GatewayTradingClientError(f"Gateway orders request failed: {result['error']}")

        logger.error(
            "gateway_trading_orders_malformed_response",
            event_type="GATEWAY_ORDERS_MALFORMED_RESPONSE",
            response_type=type(result).__name__,
            response_preview=repr(result)[:500],
        )
        raise GatewayTradingClientError("Gateway orders response was not a list")

    async def get_account_activities(self, activity_types: str | None = None) -> list[dict[str, Any]]:
        """List Alpaca account activities (broker truth of money moved).

        Proxies ``GET /api/v1/alpaca/account/activities``. ``activity_types`` is
        a comma-separated filter (e.g. ``"FILL"``); ``None`` returns all types.

        Limitation: the Gateway endpoint exposes neither date filtering nor
        pagination and the Alpaca FILL activity carries ``order_id`` + ``symbol``
        but NOT ``client_order_id`` — so orion attribution of an activity would
        require joining its ``order_id`` back to a known orion-minted order. The
        PnL-reconciliation job therefore does NOT use this surface; it
        reconstructs realized PnL from orion's own ``fills`` table instead (see
        ``jobs/reconcile_pnl.py``).
        """
        result = await self._request(
            "GET",
            "/api/v1/alpaca/account/activities",
            params={"activity_types": activity_types} if activity_types else None,
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "error" in result:
            logger.error(
                "gateway_trading_activities_request_failed",
                event_type="GATEWAY_ACTIVITIES_REQUEST_FAILED",
                error=result["error"],
            )
            raise GatewayTradingClientError(f"Gateway activities request failed: {result['error']}")
        logger.error(
            "gateway_trading_activities_malformed_response",
            event_type="GATEWAY_ACTIVITIES_MALFORMED_RESPONSE",
            response_type=type(result).__name__,
            response_preview=repr(result)[:500],
        )
        raise GatewayTradingClientError("Gateway activities response was not a list")

    async def get_order(self, order_id: str) -> dict[str, Any]:
        """Get a specific order by ID."""
        result = await self._request("GET", f"/api/v1/alpaca/orders/{order_id}")
        return _normalize_order_attribution(result)

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel an order."""
        return await self._request("DELETE", f"/api/v1/alpaca/orders/{order_id}")

    # ── Market Data (used for price discovery + option chain) ──

    async def get_stock_snapshot(self, symbol: str) -> dict[str, Any]:
        """Get latest quote/trade/bar snapshot for a stock."""
        return await self._request("GET", f"/api/v1/alpaca/stocks/{symbol}/snapshot")

    async def get_option_chain(self, underlying: str, limit: int = 1000) -> dict[str, Any]:
        """Get full options chain for an underlying."""
        return await self._request(
            "GET",
            f"/api/v1/alpaca/options/chain/{underlying}",
            params={"limit": limit},
        )

    async def get_option_quote(self, option_symbol: str) -> dict[str, Any]:
        """Return a fresh ``{bid, ask, last, timestamp}`` for a single OCC option
        contract, sourced from its underlying's live chain.

        The Gateway exposes no single-contract option-quote endpoint, so this
        fetches the underlying chain (short-TTL cached) and locates the contract
        by ``contract_symbol``. Returns ``{}`` if the symbol can't be parsed, the
        chain is unavailable, or the contract isn't found — the caller must treat
        an empty/biddless result as "no fresh quote" and fall back. Used to price
        an options close at the live market instead of a possibly-stale tracked
        mark.
        """
        import time

        from orion.shared.utils import parse_occ_symbol

        parsed = parse_occ_symbol(option_symbol)
        underlying = parsed.get("underlying")
        if not underlying or not isinstance(underlying, str):
            return {}

        cached = self._chain_cache.get(underlying)
        now = time.monotonic()
        if cached is not None and (now - cached[0]) < self._chain_cache_ttl_s:
            contracts = cached[1]
        else:
            chain = await self.get_option_chain(underlying)
            if not isinstance(chain, dict) or "error" in chain:
                return {}
            contracts = chain.get("contracts") or []
            if not isinstance(contracts, list):
                return {}
            self._chain_cache[underlying] = (now, contracts)

        for c in contracts:
            if isinstance(c, dict) and c.get("contract_symbol") == option_symbol:

                def _f(v: Any) -> float | None:
                    try:
                        return float(v) if v is not None else None
                    except (TypeError, ValueError):
                        return None

                return {
                    "bid": _f(c.get("bid")),
                    "ask": _f(c.get("ask")),
                    "last": _f(c.get("last")),
                    "timestamp": c.get("timestamp"),
                }
        return {}

    # ── Clock ────────────────────────────────────────────────

    async def get_clock(self) -> dict[str, Any]:
        """Get market clock status."""
        return await self._request("GET", "/api/v1/alpaca/clock")


# ── Singleton ────────────────────────────────────────────────

_gateway_trading_client: GatewayTradingClient | None = None


def get_gateway_trading_client() -> GatewayTradingClient:
    """Get or create Gateway trading client singleton."""
    global _gateway_trading_client
    if _gateway_trading_client is None:
        _gateway_trading_client = GatewayTradingClient()
    return _gateway_trading_client
