import pytest
from httpx import AsyncClient, ASGITransport
from orion.api.main import app

@pytest.mark.asyncio
async def test_root_endpoint_ux():
    """
    Test that the root endpoint provides helpful developer links.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/")

    assert response.status_code == 200
    data = response.json()

    # Check basic structure
    assert "app" in data
    assert "links" in data

    links = data["links"]

    # Verify presence of key developer links
    assert links["docs"] == "/docs"
    assert links["redoc"] == "/redoc"
    assert links["health"] == "/health"

    # Verify resource links are present (HATEOAS-lite)
    expected_resources = [
        "solvers",
        "experiments",
        "metrics",
        "promotions",
        "search",
        "rollups",
        "flows"
    ]

    for resource in expected_resources:
        assert resource in links
        assert links[resource] == f"/{resource}"
