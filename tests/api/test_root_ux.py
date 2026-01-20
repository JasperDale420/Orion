import pytest
from httpx import ASGITransport, AsyncClient
from orion.api.main import app


@pytest.mark.asyncio
async def test_root_endpoint_returns_friendly_message():
    """
    Test that the root endpoint returns a friendly welcome message.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    json_resp = response.json()
    assert "Welcome to Orion Admin API! 🚀" in json_resp["message"]
    assert json_resp["docs_url"] == "/docs"
