"""
Shared MCP Server Client.

Client for the Shared-MCP-Server providing Alpaca and Unusual Whales tools.
Uses HTTP to call MCP tools for trading, market data, and flow analysis.
"""

from typing import Any

import httpx

from orion.config import system_settings
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.clients.mcp_server")

# Configuration (from SystemSettings)
MCP_SERVER_URL = system_settings.mcp_server_url


class MCPServerClient:
    """
    Client for Shared-MCP-Server.

    Provides access to Alpaca trading/market data and Unusual Whales flow data.
    """

    def __init__(
        self,
        base_url: str = MCP_SERVER_URL,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _call_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """
        Call an MCP tool via HTTP.

        The MCP server exposes tools via a JSON-RPC style interface.
        """
        client = await self._get_client()

        try:
            response = await client.post(
                "/call",
                json={"tool": tool_name, "arguments": args},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error("mcp_tool_call_failed", tool=tool_name, error=str(e))
            return {"error": str(e)}
        except Exception as e:
            logger.warning("mcp_server_error", tool=tool_name, error=str(e))
            return {"error": str(e)}

    # =========== Alpaca Trading ===========

    async def get_account(self) -> dict[str, Any]:
        """Get Alpaca account details."""
        return await self._call_tool("get_account", {})

    async def get_positions(self) -> list[dict[str, Any]]:
        """Get all open positions."""
        result = await self._call_tool("get_all_positions", {})
        return result.get("positions", []) if "error" not in result else []

    async def get_position(self, symbol: str) -> dict[str, Any]:
        """Get position for a specific symbol."""
        return await self._call_tool("get_open_position", {"symbol": symbol})

    async def place_market_order(
        self,
        symbol: str,
        qty: float,
        side: str,  # "buy" or "sell"
        time_in_force: str = "day",
    ) -> dict[str, Any]:
        """Place a market order."""
        return await self._call_tool(
            "place_order",
            {
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "type": "market",
                "time_in_force": time_in_force,
            },
        )

    async def place_stop_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        stop_price: float,
    ) -> dict[str, Any]:
        """Place a stop order."""
        return await self._call_tool(
            "place_stop_order",
            {
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "stop_price": stop_price,
            },
        )

    async def close_position(self, symbol: str, qty: float | None = None) -> dict[str, Any]:
        """Close a position (full or partial)."""
        args: dict[str, Any] = {"symbol": symbol}
        if qty is not None:
            args["qty"] = qty
        return await self._call_tool("close_position", args)

    async def get_portfolio_history(self, period: str = "1M") -> dict[str, Any]:
        """Get portfolio equity history."""
        return await self._call_tool("get_portfolio_history", {"period": period})

    # =========== Alpaca Market Data ===========

    async def get_stock_snapshot(self, symbol: str) -> dict[str, Any]:
        """Get latest quote, trade, and bar for a stock."""
        return await self._call_tool("get_stock_snapshot", {"symbol": symbol})

    async def get_stock_bars(
        self,
        symbol: str,
        timeframe: str = "1Day",
        start: str | None = None,
        end: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get historical bars."""
        args = {"symbol": symbol, "timeframe": timeframe, "limit": limit}
        if start:
            args["start"] = start
        if end:
            args["end"] = end
        result = await self._call_tool("get_stock_bars", args)
        return result.get("bars", []) if "error" not in result else []

    async def get_market_clock(self) -> dict[str, Any]:
        """Get market clock status."""
        return await self._call_tool("get_clock", {})

    async def get_news(self, symbols: list[str], limit: int = 10) -> list[dict[str, Any]]:
        """Get recent news for symbols."""
        result = await self._call_tool("get_news", {"symbols": symbols, "limit": limit})
        return result.get("news", []) if "error" not in result else []

    async def get_option_chain(self, underlying: str) -> dict[str, Any]:
        """Get option chain for underlying."""
        return await self._call_tool("get_option_chain", {"underlying_symbol": underlying})

    # =========== Unusual Whales ===========

    async def get_flow_alerts(self, ticker: str | None = None) -> list[dict[str, Any]]:
        """Get unusual options flow alerts."""
        args = {}
        if ticker:
            args["ticker"] = ticker
        result = await self._call_tool("get_flow_alerts", args)
        return result.get("alerts", []) if "error" not in result else []

    async def get_market_tide(self) -> dict[str, Any]:
        """Get market-wide buying/selling pressure."""
        return await self._call_tool("get_market_tide", {})

    async def get_greek_flow(self, ticker: str) -> dict[str, Any]:
        """Get greek exposure for a ticker."""
        return await self._call_tool("get_greek_flow", {"ticker": ticker})

    async def get_sector_flow(self) -> dict[str, Any]:
        """Get flow aggregated by sector."""
        return await self._call_tool("get_sector_flow", {})

    async def get_seasonality(self, ticker: str) -> dict[str, Any]:
        """Get monthly seasonality trends."""
        return await self._call_tool("get_seasonality", {"ticker": ticker})

    async def get_analyst_ratings(self, ticker: str) -> dict[str, Any]:
        """Get analyst ratings and upgrades/downgrades."""
        return await self._call_tool("get_analyst_ratings", {"ticker": ticker})


# Singleton instance
_mcp_client: MCPServerClient | None = None


def get_mcp_client() -> MCPServerClient:
    """Get or create MCP server client singleton."""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPServerClient()
    return _mcp_client
