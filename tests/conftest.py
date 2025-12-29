import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# --- 1. Global Environment Setup (Pre-Import) ---
# Must happen before any orion modules are imported to ensure Settings pick these up.
os.environ["ORION_STAGE"] = "test"
os.environ["DB_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["REDPANDA_BROKERS"] = "mock:9092"
os.environ["ALPACA_API_KEY"] = "mock_key"
os.environ["ALPACA_SECRET_KEY"] = "mock_secret"
os.environ["ALPACA_PAPER"] = "True"
os.environ["OPENAI_API_KEY"] = "mock_openai_key"

# Ensure `src/` is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

# --- 2. Import-Time Mocking ---
# Global mock for pandas_ta (missing dep)
try:
    import pandas_ta  # noqa: F401
except ImportError:
    sys.modules["pandas_ta"] = MagicMock()

import asyncio

import pytest

# --- 3. Fixtures ---


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test session."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
async def mock_redpanda_producer(monkeypatch):
    """
    Globally mock RedpandaProducer to prevent network calls.
    Autouse=True ensures it applies to every test logic that might lazily instantiate it.
    """
    from orion.connectors.redpanda_producer import RedpandaProducer

    mock_instance = MagicMock(spec=RedpandaProducer)
    mock_instance.start = AsyncMock()
    mock_instance.stop = AsyncMock()
    mock_instance.produce_event = AsyncMock()

    # Patch the singleton using AsyncSingleton's _instances dict
    # AsyncSingleton uses _instances[cls] not _instance
    async def mock_get_instance(*args, **kwargs):
        return mock_instance

    monkeypatch.setattr(RedpandaProducer, "get_instance", mock_get_instance)

    yield mock_instance


@pytest.fixture(autouse=True)
async def setup_test_db(monkeypatch):
    """
    Overrides the DB engine to use in-memory SQLite for all tests.
    Ensures tables are created before each test and dropped after.
    """
    from orion.storage import db

    # Force test DB URL again just in case
    test_db_url = "sqlite+aiosqlite:///:memory:"

    # Bind new engine
    old_engine = db.engine

    # Re-configure DB for this test session (idempotent if already set, but good for safety)
    db.configure_db(test_db_url, echo=False)

    # Create Tables
    try:
        await db.init_db()
    except Exception as e:
        # If init_db fails (e.g. concurrent issues), try to recover or fail loud
        pytest.fail(f"DB Init Failed: {e}")

    yield

    # Teardown
    # In :memory:, proceed to drop everything to keep state clean between tests if the engine is shared
    # Since we are using a global loop scope for now but potentially function scope DB,
    # dropping tables is safer than relying on memory wipe if the engine persists.
    if db.engine:
        async with db.engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.drop_all)
        await db.engine.dispose()

    # Restore (mostly to be polite, though strictly not needed in a test process)
    if old_engine:
        monkeypatch.setattr(db, "engine", old_engine)


@pytest.fixture
def risk_manager_factory():
    """Factory fixture to create RiskManager instances with custom config."""
    from orion.execution.risk_manager import RiskManager

    def _create(config=None):
        return RiskManager(config=config)

    return _create
