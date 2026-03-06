"""
Tests for the Orion Admin API endpoints.
Uses httpx.AsyncClient with FastAPI dependency overrides.
"""

from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient

from orion.api.main import app


@pytest.fixture
def override_deps() -> Generator[AsyncMock, None, None]:
    """Override API dependencies for testing."""

    # Mock API key auth
    async def mock_api_key() -> None:
        return None

    # Mock DB session
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_result.scalars.return_value.first.return_value = None
    mock_result.first.return_value = None
    mock_session.execute.return_value = mock_result
    mock_session.commit = AsyncMock()

    async def mock_get_db() -> AsyncMock:
        return mock_session

    # Apply overrides
    from orion.api.auth import require_api_key
    from orion.api.deps import get_db

    app.dependency_overrides[require_api_key] = mock_api_key
    app.dependency_overrides[get_db] = mock_get_db

    yield mock_session

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
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestSolversEndpoints:
    """Tests for /solvers endpoints."""

    @pytest.mark.asyncio
    async def test_list_solvers_empty(
        self,
        override_deps: AsyncMock,
        mock_audit_logging: None,
    ) -> None:
        """List solvers should return empty list when no solvers exist."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/solvers")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_get_solver_not_found(
        self,
        override_deps: AsyncMock,
        mock_audit_logging: None,
    ) -> None:
        """Get solver should return 404 for non-existent solver."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/solvers/nonexistent-solver")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestMetricsEndpoint:
    """Tests for /metrics endpoint."""

    @pytest.mark.asyncio
    async def test_list_metrics_empty(
        self,
        override_deps: AsyncMock,
        mock_audit_logging: None,
    ) -> None:
        """List metrics should return empty list when no metrics exist."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/metrics")

        assert response.status_code == 200
        assert response.json() == []


class TestExperimentsEndpoint:
    """Tests for /experiments endpoint."""

    @pytest.mark.asyncio
    async def test_list_experiments_empty(
        self,
        override_deps: AsyncMock,
        mock_audit_logging: None,
    ) -> None:
        """List experiments should return empty list when no experiments exist."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/experiments")

        assert response.status_code == 200
        assert response.json() == []


class TestEventsEndpoint:
    """Tests for /events/{event_id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_event_not_found(
        self,
        override_deps: AsyncMock,
        mock_audit_logging: None,
    ) -> None:
        """Get event should return 404 for non-existent event."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/events/nonexistent-event")

        assert response.status_code == 404


class TestCandidatesEndpoint:
    """Tests for /candidates/{candidate_id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_candidate_not_found(
        self,
        override_deps: AsyncMock,
        mock_audit_logging: None,
    ) -> None:
        """Get candidate should return 404 for non-existent candidate."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/candidates/nonexistent-candidate")

        assert response.status_code == 404


class TestPromotionsEndpoints:
    """Tests for /promotions/recommendations endpoints."""

    @pytest.mark.asyncio
    async def test_list_promotion_recommendations_empty(
        self,
        override_deps: AsyncMock,
        mock_audit_logging: None,
    ) -> None:
        """List promotion recommendations should return empty list when none exist."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/promotions")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_approve_promotion_not_found(
        self,
        override_deps: AsyncMock,
        mock_audit_logging: None,
    ) -> None:
        """Approve promotion should return 404 for non-existent recommendation."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/promotions/nonexistent/approve",
                params={"reviewed_by": "test_user"},
            )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_reject_promotion_not_found(
        self,
        override_deps: AsyncMock,
        mock_audit_logging: None,
    ) -> None:
        """Reject promotion should return 404 for non-existent recommendation."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/promotions/nonexistent/reject",
                params={"reviewed_by": "test_user"},
            )

        assert response.status_code == 404


class TestSearchEndpoint:
    """Tests for /search endpoint."""

    @pytest.mark.asyncio
    async def test_search_requires_query(
        self,
        override_deps: AsyncMock,
        mock_audit_logging: None,
    ) -> None:
        """Search should return 422 when query parameter is missing."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/search")

        assert response.status_code == 422


class TestRollupsEndpoints:
    """Tests for /rollups endpoints."""

    @pytest.mark.asyncio
    async def test_get_rollups_requires_ticker(
        self,
        override_deps: AsyncMock,
        mock_audit_logging: None,
    ) -> None:
        """Get rollups should return 422 when ticker is missing."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/rollups")

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_get_rollups_with_ticker(
        self,
        override_deps: AsyncMock,
        mock_audit_logging: None,
    ) -> None:
        """Get rollups should return data for valid ticker."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/rollups", params={"ticker": "AAPL"})

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_get_rollup_not_found(
        self,
        override_deps: AsyncMock,
        mock_audit_logging: None,
    ) -> None:
        """Get specific rollup should return 404 when not found."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/rollups/AAPL/5m/2025-01-01T00:00:00")

        assert response.status_code == 404


class TestFlowsEndpoint:
    """Tests for /flows endpoint."""

    @pytest.mark.asyncio
    async def test_get_flows_empty(
        self,
        override_deps: AsyncMock,
        mock_audit_logging: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Get flows should return empty list when no flows exist."""

        class _FakeReader:
            def read_flow(self, **_kwargs):  # type: ignore[no-untyped-def]
                return pd.DataFrame()

        monkeypatch.setattr("orion.api.main.get_heber_reader", lambda: _FakeReader())

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/flows")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_get_flows_with_filters(
        self,
        override_deps: AsyncMock,
        mock_audit_logging: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Get flows should accept filter parameters."""

        class _FakeReader:
            def read_flow(self, **_kwargs):  # type: ignore[no-untyped-def]
                return pd.DataFrame()

        monkeypatch.setattr("orion.api.main.get_heber_reader", lambda: _FakeReader())

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/flows", params={"ticker": "TSLA", "min_premium_usd": 10000})

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_list_solvers_returns_mapping_rows(
        self,
        override_deps: AsyncMock,
        mock_audit_logging: None,
    ) -> None:
        """List solvers should accept mapping rows without ORM hydration."""
        override_deps.execute.return_value.mappings.return_value.all.return_value = [
            {
                "solver_id": "solver_1",
                "family_name": "TrendRider",
                "stage": "research",
                "is_active": False,
                "config": {"param": 1},
                "created_at_utc": datetime(2025, 1, 1, tzinfo=UTC),
                "total_pnl": 0.0,
                "sharpe_ratio": 0.0,
                "win_rate": 0.0,
                "trades_count": 0,
            }
        ]
        override_deps.execute.return_value.scalars.return_value.all.return_value = []

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/solvers")

        assert response.status_code == 200
        payload = response.json()
        assert payload[0]["solver_id"] == "solver_1"
