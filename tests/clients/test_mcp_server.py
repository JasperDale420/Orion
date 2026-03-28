"""
Tests for MCPServerClient.

Tests the MCP Server client with mocked HTTP responses.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.clients.mcp_server import MCPServerClient, get_mcp_client


class TestMCPServerClient:
    """Tests for MCPServerClient class."""

    @pytest.fixture
    def client(self) -> MCPServerClient:
        """Create a client instance."""
        return MCPServerClient(base_url="http://test:8001")

    @pytest.mark.asyncio
    async def test_call_tool_success(self, client: MCPServerClient) -> None:
        """Test successful tool call."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = lambda: {"result": "success"}

        with patch.object(client, "_get_client") as mock_get_client:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http

            result = await client._call_tool("test_tool", {"arg": "value"})

            assert result["result"] == "success"

    @pytest.mark.asyncio
    async def test_call_tool_failure(self, client: MCPServerClient) -> None:
        """Test tool call failure returns error."""
        with patch.object(client, "_get_client") as mock_get_client:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(side_effect=Exception("Connection failed"))
            mock_get_client.return_value = mock_http

            result = await client._call_tool("test_tool", {})

            assert "error" in result

    @pytest.mark.asyncio
    async def test_get_account(self, client: MCPServerClient) -> None:
        """Test get_account calls correct tool."""
        with patch.object(client, "_call_tool") as mock_call:
            mock_call.return_value = {"equity": 100000}

            result = await client.get_account()

            mock_call.assert_called_once_with("get_account", {})
            assert result["equity"] == 100000

    @pytest.mark.asyncio
    async def test_get_positions(self, client: MCPServerClient) -> None:
        """Test get_positions returns list."""
        with patch.object(client, "_call_tool") as mock_call:
            mock_call.return_value = {
                "positions": [
                    {"symbol": "AAPL", "qty": 100},
                    {"symbol": "MSFT", "qty": 50},
                ]
            }

            positions = await client.get_positions()

            assert len(positions) == 2
            assert positions[0]["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_get_positions_handles_error(self, client: MCPServerClient) -> None:
        """Test get_positions returns empty on error."""
        with patch.object(client, "_call_tool") as mock_call:
            mock_call.return_value = {"error": "Connection failed"}

            positions = await client.get_positions()

            assert positions == []

    @pytest.mark.asyncio
    async def test_place_market_order(self, client: MCPServerClient) -> None:
        """Test place_market_order builds correct args."""
        with patch.object(client, "_call_tool") as mock_call:
            mock_call.return_value = {"order_id": "123"}

            await client.place_market_order(
                symbol="AAPL",
                qty=10,
                side="buy",
            )

            mock_call.assert_called_once()
            call_args = mock_call.call_args[0]
            assert call_args[0] == "place_order"
            assert call_args[1]["symbol"] == "AAPL"
            assert call_args[1]["qty"] == 10
            assert call_args[1]["side"] == "buy"
            assert call_args[1]["type"] == "market"

    @pytest.mark.asyncio
    async def test_get_flow_alerts(self, client: MCPServerClient) -> None:
        """Test get_flow_alerts with ticker filter."""
        with patch.object(client, "_call_tool") as mock_call:
            mock_call.return_value = {"alerts": [{"ticker": "SPY", "premium": 500000}]}

            alerts = await client.get_flow_alerts(ticker="SPY")

            mock_call.assert_called_once_with("get_flow_alerts", {"ticker": "SPY"})
            assert len(alerts) == 1

    @pytest.mark.asyncio
    async def test_get_market_tide(self, client: MCPServerClient) -> None:
        """Test get_market_tide."""
        with patch.object(client, "_call_tool") as mock_call:
            mock_call.return_value = {"tide": "bullish", "strength": 0.7}

            result = await client.get_market_tide()

            mock_call.assert_called_once_with("get_market_tide", {})
            assert result["tide"] == "bullish"

    @pytest.mark.asyncio
    async def test_close_client(self, client: MCPServerClient) -> None:
        """Test client close is idempotent."""
        await client.close()
        await client.close()


class TestGetMCPClient:
    """Tests for get_mcp_client singleton."""

    def test_get_mcp_client_returns_instance(self) -> None:
        """Test get_mcp_client returns MCPServerClient."""
        import orion.clients.mcp_server as module

        module._mcp_client = None

        client = get_mcp_client()

        assert isinstance(client, MCPServerClient)

    def test_get_mcp_client_singleton(self) -> None:
        """Test get_mcp_client returns same instance."""
        client1 = get_mcp_client()
        client2 = get_mcp_client()

        assert client1 is client2
