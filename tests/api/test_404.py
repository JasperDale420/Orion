import pytest
from httpx import ASGITransport, AsyncClient
from orion.api.main import app

@pytest.mark.asyncio
async def test_custom_404_handler():
    """
    Test that the custom 404 handler returns a friendly JSON response.
    """
    # Use ASGITransport to avoid DeprecationWarning/TypeError with newer httpx
    # (Same pattern as in test_flow_filters.py)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Request a non-existent route
        response = await client.get("/this-route-does-not-exist-12345")

    assert response.status_code == 404
    data = response.json()
    assert data["detail"] == "Resource not found"
    assert "suggestion" in data
