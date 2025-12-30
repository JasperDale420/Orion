import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class MCPClient:
    """
    A lightweight SSE client for the Model Context Protocol (MCP).
    Connects to an MCP server running in SSE mode to list and execute tools.
    """

    def __init__(self, base_url: str = "http://mcp-server:8001"):
        self.base_url = base_url.rstrip("/")
        self.sse_url = f"{self.base_url}/sse"
        self.messages_url = f"{self.base_url}/messages"  # Per session, usually
        self.session_id: Optional[str] = None
        self.tools: List[Dict[str, Any]] = []

    async def connect(self) -> None:
        """
        Establishes connection to fetch tools.
        Note: True SSE persistence is complex; for this client we mainly need to know
        the tools endpoint is reachable and we might rely on stateless HTTP POST for execution
        if the server supports Direct invocation (many do) OR we just use the discovery.

        Wait, standard MCP over SSE requires:
        1. GET /sse -> returns session_id in an SSE event 'endpoint'.
        2. POST /messages?session_id=... to send JSON-RPC requests.

        Let's implement a quick handshake.
        """
        try:
            # We'll use a short-lived connection just to get the session endpoint if needed,
            # or check if we can query tools via a standard JSON-RPC endpoint if exposed separately.
            # FastMCP usually exposes tools via standard MCP lifecycle.

            # Simple discovery: Assume we can list tools if we had a transport.
            # Limitation: Implementing a full MCP Client in 50 lines is hard without the SDK.
            # But we can try a direct POST if FastMCP supports a simple HTTP/JSON-RPC gateway
            # or if we strictly follow the SSE handshake.

            # Strategy: Start an SSE stream, wait for the 'endpoint' event, then use that to list tools.
            async with httpx.AsyncClient(timeout=5.0) as client:
                async with client.stream("GET", self.sse_url) as response:
                    async for line in response.aiter_lines():
                        if line.startswith("event: endpoint"):
                            # Next line is data: ...
                            continue
                        if line.startswith("data:"):
                            # This is the endpoint path (e.g. /messages/?session_id=...)
                            path = line[5:].strip()
                            self.messages_url = f"{self.base_url}{path}"
                            logger.info(f"MCP Connected. Session URL: {self.messages_url}")
                            return

        except Exception as e:
            logger.error(f"Failed to connect to MCP Server: {e}")
            # Fallback (maybe server isn't ready)

    async def list_tools(self) -> List[Dict[str, Any]]:
        """
        Sends a 'tools/list' JSON-RPC request.
        """
        if not self.messages_url:
            await self.connect()

        if not self.messages_url:
            return []

        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                resp = await client.post(self.messages_url, json=payload)
                resp.raise_for_status()
                data = resp.json()

                if "result" in data:
                    self.tools = data["result"].get("tools", [])
                    return self.tools
                if "error" in data:
                    logger.error(f"MCP List Tools Error: {data['error']}")
        except Exception as e:
            logger.error(f"MCP List Tools Failed: {e}")

        return []

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """
        Executes a tool call via 'tools/call'.
        """
        if not self.messages_url:
            await self.connect()

        payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}}

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.post(self.messages_url, json=payload)
                resp.raise_for_status()
                data = resp.json()

                if "result" in data:
                    # Result usually contains { "content": [ {"type": "text", "text": "..."} ] }
                    content = data["result"].get("content", [])
                    # Flatten text
                    return "\n".join([c.get("text", "") for c in content if c.get("type") == "text"])
                if "error" in data:
                    return f"Error: {data['error'].get('message')}"

        except Exception as e:
            return f"RPC Failed: {str(e)}"
