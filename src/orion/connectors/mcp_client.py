from typing import Any, Dict, List, Optional

import structlog

from orion.core.http_client import create_async_http_client

logger = structlog.get_logger(__name__)


class MCPClient:
    """
    A lightweight SSE client for the Model Context Protocol (MCP).
    Connects to an MCP server running in SSE mode to list and execute tools.
    """

    def __init__(self, base_url: str = "http://mcp-server:8001"):
        self.base_url = base_url.rstrip("/")
        self.sse_url = f"{self.base_url}/sse"
        self.messages_url = f"{self.base_url}/messages"
        self.session_id: Optional[str] = None
        self.tools: List[Dict[str, Any]] = []

    async def connect(self) -> None:
        """
        Establishes connection by performing SSE handshake.

        Standard MCP over SSE requires:
        1. GET /sse -> returns session_id in an SSE event 'endpoint'.
        2. POST /messages?session_id=... to send JSON-RPC requests.
        """
        try:
            async with create_async_http_client(timeout=5.0) as client:
                async with client.stream("GET", self.sse_url) as response:
                    async for line in response.aiter_lines():
                        if line.startswith("event: endpoint"):
                            continue
                        if line.startswith("data:"):
                            path = line[5:].strip()
                            self.messages_url = f"{self.base_url}{path}"
                            logger.info("mcp_connected", session_url=self.messages_url)
                            return

        except (ConnectionError, OSError) as exc:
            logger.error("mcp_connect_failed", error=str(exc), exc_info=True)

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Sends a 'tools/list' JSON-RPC request."""
        if not self.messages_url:
            await self.connect()

        if not self.messages_url:
            return []

        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

        try:
            async with create_async_http_client() as client:
                resp = await client.post(self.messages_url, json=payload)
                resp.raise_for_status()
                data = resp.json()

                if "result" in data:
                    self.tools = data["result"].get("tools", [])
                    return self.tools
                if "error" in data:
                    logger.error("mcp_list_tools_error", error=data["error"])
        except (ConnectionError, OSError) as exc:
            logger.error("mcp_list_tools_failed", error=str(exc), exc_info=True)

        return []

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """Executes a tool call via 'tools/call'."""
        if not self.messages_url:
            await self.connect()

        payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}}

        try:
            async with create_async_http_client(timeout=30.0) as client:
                resp = await client.post(self.messages_url, json=payload)
                resp.raise_for_status()
                data = resp.json()

                if "result" in data:
                    content = data["result"].get("content", [])
                    return "\n".join([c.get("text", "") for c in content if c.get("type") == "text"])
                if "error" in data:
                    return f"Error: {data['error'].get('message')}"

        except (ConnectionError, OSError) as exc:
            return f"RPC Failed: {exc}"
