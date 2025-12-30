"""
Tests for the Orion Admin API endpoints.
Uses httpx.AsyncClient with FastAPI dependency overrides.
"""

from typing import Any, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from orion.api.main import app


@pytest.fixture
def override_deps() -> Generator[None, None, None]:
    """Override API dependencies for testing."""
    # Mock API key auth
    async def mock_api_key() -> None:
        return None

    # Mock DB session
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_result.scalars.return_value.first.return_value = None
    mock_session.execute.return_value = mock_result
    mock_session.commit = AsyncMock()

    async def mock_get_db() -> AsyncMock:
        return mock_session

    # Apply overrides
    from orion.api.auth import require_api_key
    from orion.api.deps import get_db

    app.dependency_overrides[require_api_key] = mock_api_key
    app.dependency_overrides[get_db] = mock_get_db

    yield

    # Clean up
    app.dependency_overrides.clear()


@pytest.fixture
def mock_audit_logging() -> Generator[None, None, None]:
    """Disable audit logging for tests."""
    with patch("orion.api.main.db_write", new_callable=AsyncMock):
        yield


class TestHealthEndpoint:
    """Tests for /health endpoint."""

    @pytest.mark.asyncio
    async def test_health_check_returns_ok(self, mock_audit_logging: None) -> None:
        """Health endpoint should return status ok."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestSolversEndpoints:
    """Tests for /solvers endpoints."""

    @pytest.mark.asyncio
    async def test_list_solvers_empty(
        self,
        override_deps: None,
        mock_audit_logging: None,
    ) -> None:
        """List solvers should return empty list when no solvers exist."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/solvers")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_get_solver_not_found(
        self,
        override_deps: None,
        mock_audit_logging: None,
    ) -> None:
        """Get solver should return 404 for non-existent solver."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/solvers/nonexistent-solver")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestMetricsEndpoint:
    """Tests for /metrics endpoint."""

    @pytest.mark.asyncio
    async def test_list_metrics_empty(
        self,
        override_deps: None,
        mock_audit_logging: None,
    ) -> None:
        """List metrics should return empty list when no metrics exist."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/metrics")

        assert response.status_code == 200
        assert response.json() == []


class TestExperimentsEndpoint:
    """Tests for /experiments endpoint."""

    @pytest.mark.asyncio
    async def test_list_experiments_empty(
        self,
        override_deps: None,
        mock_audit_logging: None,
    ) -> None:
        """List experiments should return empty list when no experiments exist."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/experiments")

        assert response.status_code == 200
        assert response.json() == []


class TestEventsEndpoint:
    """Tests for /events/{event_id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_event_not_found(
        self,
        override_deps: None,
        mock_audit_logging: None,
    ) -> None:
        """Get event should return 404 for non-existent event."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/events/nonexistent-event")

        assert response.status_code == 404


class TestCandidatesEndpoint:
    """Tests for /candidates/{candidate_id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_candidate_not_found(
        self,
        override_deps: None,
        mock_audit_logging: None,
    ) -> None:
        """Get candidate should return 404 for non-existent candidate."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/candidates/nonexistent-candidate")

        assert response.status_code == 404
