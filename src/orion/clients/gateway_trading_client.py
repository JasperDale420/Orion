"""
Data Gateway Trading Client.

Thin async httpx wrapper around the Data Gateway's Alpaca trading endpoints.
Routes all order management, position tracking, and account queries through
the centralized Data Gateway REST API.
"""

from typing import Any

import httpx
import structlog

from orion.config import system_settings

logger = structlog.get_logger("orion.clients.gateway_trading")

_RESPONSE_DATA_KEY = "data"


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
            logger.error(
                "gateway_trading_http_error",
                method=method,
                path=path,
                status=exc.response.status_code,
                detail=exc.response.text[:500],
            )
            return {"error": str(exc)}
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
            return []
        return [result] if result else []

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
    ) -> dict[str, Any]:
        """Submit a new order through the Gateway."""
        body: dict[str, Any] = {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "order_type": order_type,
            "time_in_force": time_in_force,
        }
        if limit_price is not None:
            body["limit_price"] = limit_price
        if client_order_id is not None:
            body["client_order_id"] = client_order_id
        return await self._request("POST", "/api/v1/alpaca/orders", json_body=body)

    async def get_orders(self, status: str = "open", limit: int = 50) -> list[dict[str, Any]]:
        """List orders with optional status filter."""
        result = await self._request(
            "GET",
            "/api/v1/alpaca/orders",
            params={"status": status, "limit": limit},
        )
        if isinstance(result, list):
            return result
        return []

    async def get_order(self, order_id: str) -> dict[str, Any]:
        """Get a specific order by ID."""
        return await self._request("GET", f"/api/v1/alpaca/orders/{order_id}")

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
