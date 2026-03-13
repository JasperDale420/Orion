from fastapi.testclient import TestClient

from orion.api.main import app


def test_custom_404_handler():
    client = TestClient(app)
    response = client.get("/non-existent-endpoint-for-testing-404")
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data
    assert data["detail"] == "Resource not found"
    assert "suggestion" in data
    assert data["suggestion"] == "Check the URL for typos."
    # Ensure we are NOT leaking endpoints
    assert "valid_endpoints" not in data
