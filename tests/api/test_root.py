import pytest
from httpx import ASGITransport, AsyncClient

from orion.api.main import app


@pytest.mark.asyncio
async def test_root_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/")

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Welcome to Orion Admin API! 🚀"
    assert data["app"] == "Orion Admin API"
    assert data["version"] == "1.0.0"
    assert data["status"] == "operational"
    assert "timestamp_utc" in data
    assert data["links"]["docs"] == "/docs"
    assert data["links"]["health"] == "/health"


@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
