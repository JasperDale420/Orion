
import pytest
from httpx import ASGITransport, AsyncClient

from orion.api.main import app


class TestRootEndpoint:
    """Tests for the root endpoint."""

    @pytest.mark.asyncio
    async def test_root_endpoint_returns_welcome_message(self) -> None:
        """Root endpoint should return a helpful welcome message."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Welcome to Orion Admin API"
        assert "docs_url" in data
        assert "redoc_url" in data
        assert "health_check" in data
