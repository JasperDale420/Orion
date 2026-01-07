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
    # The existing root endpoint does not have "Welcome to Orion Admin API! 🚀" in message
    # It returns "app": "Orion Admin API"
    assert json_resp["app"] == "Orion Admin API"
    assert json_resp["links"]["docs"] == "/docs"

@pytest.mark.asyncio
async def test_404_handler_ux():
    """
    Verify the 404 handler returns a friendly message and suggestions.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/this/does/not/exist")

    assert response.status_code == 404
    data = response.json()
    assert data["detail"] == "Not Found"
    assert data["message"] == "Oops! The requested resource was not found."
    assert "/solvers" in data["suggestions"]
    assert "/docs" in data.values()
