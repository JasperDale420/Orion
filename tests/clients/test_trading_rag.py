"""
Tests for TradingRAGClient.

Tests the TradingRAG client with mocked HTTP responses.
"""

from unittest.mock import AsyncMock, patch

import pytest

from orion.clients.trading_rag import TradingRAGClient, get_rag_client


class TestTradingRAGClient:
    """Tests for TradingRAGClient class."""

    @pytest.fixture
    def client(self) -> TradingRAGClient:
        """Create a client instance."""
        return TradingRAGClient(
            base_url="http://test:8005",
            api_key="test_key",  # pragma: allowlist secret
        )

    @pytest.mark.asyncio
    async def test_retrieve_success(self, client: TradingRAGClient) -> None:
        """Test successful document retrieval."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = AsyncMock()
        # json() returns a dict, not a coroutine in httpx
        mock_response.json = lambda: {
            "results": [
                {"content": "Trading strategy tip 1", "score": 0.95},
                {"content": "Trading strategy tip 2", "score": 0.90},
            ]
        }

        with patch.object(client, "_get_client") as mock_get_client:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http

            results = await client.retrieve("best exit strategies", top_k=2)

            assert len(results) == 2
            assert results[0]["content"] == "Trading strategy tip 1"

    @pytest.mark.asyncio
    async def test_retrieve_failure_returns_empty(self, client: TradingRAGClient) -> None:
        """Test retrieval failure returns empty list."""
        with patch.object(client, "_get_client") as mock_get_client:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(side_effect=Exception("Connection failed"))
            mock_get_client.return_value = mock_http

            results = await client.retrieve("test query")

            assert results == []

    @pytest.mark.asyncio
    async def test_answer_success(self, client: TradingRAGClient) -> None:
        """Test successful answer generation."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = AsyncMock()
        mock_response.json = lambda: {
            "answer": "Use 2x ATR for stop losses in trend following.",
            "sources": [{"title": "Trend Following Book", "page": 42}],
        }

        with patch.object(client, "_get_client") as mock_get_client:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_http

            result = await client.answer("How to set stop losses?")

            assert "ATR" in result["answer"]
            assert len(result["sources"]) == 1

    @pytest.mark.asyncio
    async def test_answer_failure_returns_empty(self, client: TradingRAGClient) -> None:
        """Test answer failure returns empty dict."""
        with patch.object(client, "_get_client") as mock_get_client:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(side_effect=Exception("Timeout"))
            mock_get_client.return_value = mock_http

            result = await client.answer("test question")

            assert result["answer"] == ""
            assert result["sources"] == []

    @pytest.mark.asyncio
    async def test_ask_strategy_question_convenience(self, client: TradingRAGClient) -> None:
        """Test ask_strategy_question convenience method."""
        with patch.object(client, "answer") as mock_answer:
            mock_answer.return_value = {"answer": "Use momentum indicators", "sources": []}

            answer = await client.ask_strategy_question("What indicators work?")

            assert answer == "Use momentum indicators"

    @pytest.mark.asyncio
    async def test_close_client(self, client: TradingRAGClient) -> None:
        """Test client close is idempotent."""
        # Should not raise even when no client exists
        await client.close()
        await client.close()  # Second close should also work


class TestGetRagClient:
    """Tests for get_rag_client singleton."""

    def test_get_rag_client_returns_instance(self) -> None:
        """Test get_rag_client returns a TradingRAGClient."""
        # Reset singleton for test isolation
        import orion.clients.trading_rag as module

        module._rag_client = None

        client = get_rag_client()

        assert isinstance(client, TradingRAGClient)

    def test_get_rag_client_singleton(self) -> None:
        """Test get_rag_client returns same instance."""
        client1 = get_rag_client()
        client2 = get_rag_client()

        assert client1 is client2
