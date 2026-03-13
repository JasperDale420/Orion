from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from orion.api.deps import get_db
from orion.api.main import app
from orion.storage.models_solvers import Solver


# Mock the DB Session
class MockAsyncSession:
    def __init__(self):
        self.execute = AsyncMock()
        self.commit = AsyncMock()
        self.refresh = AsyncMock()
        self.close = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


async def override_get_db():
    session = MockAsyncSession()
    # Setup mock return values
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [
        Solver(
            solver_id="test_solver_1",
            family_name="TestFamily",
            stage="research",
            is_active=True,
            config={"test": "config"},
            created_at_utc=datetime.now(UTC),
            total_pnl=100.0,
            sharpe_ratio=1.5,
            win_rate=0.6,
            trades_count=10,
        )
    ]
    mock_result.scalars.return_value.first.return_value = Solver(
        solver_id="test_solver_1",
        family_name="TestFamily",
        stage="research",
        is_active=True,
        config={"test": "config"},
        created_at_utc=datetime.now(UTC),
        total_pnl=100.0,
        sharpe_ratio=1.5,
        win_rate=0.6,
        trades_count=10,
    )
    # /solvers now uses SQLAlchemy mapping rows to avoid ORM hydration overhead.
    mock_result.mappings.return_value.all.return_value = [
        {
            "solver_id": "test_solver_1",
            "family_name": "TestFamily",
            "stage": "research",
            "is_active": True,
            "config": {"test": "config"},
            "created_at_utc": datetime.now(UTC),
            "total_pnl": 100.0,
            "sharpe_ratio": 1.5,
            "win_rate": 0.6,
            "trades_count": 10,
        }
    ]

    session.execute.return_value = mock_result
    yield session


@pytest.fixture(autouse=True)
def _override_dependency():
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_read_solvers(monkeypatch):
    monkeypatch.setenv("ORION_API_KEY", "test_secret_key")
    headers = {"x-api-key": "test_secret_key"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/solvers", headers=headers)

    if response.status_code != 200:
        print(f"API Error Response: {response.text}")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["solver_id"] == "test_solver_1"
    assert data[0]["family_name"] == "TestFamily"


@pytest.mark.asyncio
async def test_read_solver_detail(monkeypatch):
    monkeypatch.setenv("ORION_API_KEY", "test_secret_key")
    headers = {"x-api-key": "test_secret_key"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/solvers/test_solver_1", headers=headers)

    if response.status_code != 200:
        print(f"API Error Response: {response.text}")
    assert response.status_code == 200
    data = response.json()
    assert data["solver_id"] == "test_solver_1"


@pytest.mark.asyncio
async def test_health_check(monkeypatch):
    monkeypatch.setenv("ORION_API_KEY", "test_secret_key")
    # Health might not need auth, but depends on global router.
    # Checking main.py: @app.get("/health") is open.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
