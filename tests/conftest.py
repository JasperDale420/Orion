import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

# --- 0. Exclude script files that are not proper test modules ---
# test_e2e_flow_pipeline.py runs blocking I/O at module import time and has no
# test functions — it must not be collected by pytest.
collect_ignore = [
    "integration/test_e2e_flow_pipeline.py",
]

# --- 1. Global Environment Setup (Pre-Import) ---
# Must happen before any orion modules are imported to ensure Settings pick these up.
os.environ["ORION_STAGE"] = "test"
os.environ["DB_URL"] = "sqlite+aiosqlite:///:memory:"
# Pin the flow source to the code default: the deployment .env may set
# ORION_FLOW_SOURCE=shadow/push (live shadow rollout), and load_dotenv would
# otherwise leak that into the suite — tests that exercise shadow/push set it
# explicitly on the settings object.
os.environ["ORION_FLOW_SOURCE"] = "poll"
os.environ["ALPACA_API_KEY"] = "mock_key"
os.environ["ALPACA_SECRET_KEY"] = "mock_secret"
os.environ["ALPACA_PAPER"] = "True"
os.environ["OPENAI_API_KEY"] = "mock_openai_key"
os.environ["NUMBA_DISABLE_JIT"] = "1"
os.environ["NUMBA_CACHE_DIR"] = "/tmp/numba_cache"

# Ensure `src/` is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
for dependency in ("empire-core", "empire-schemas", "empire-gateway-client"):
    dep_path = REPO_ROOT.parent / dependency
    dep_src_path = dep_path / "src"
    if str(dep_path) not in sys.path:
        sys.path.insert(0, str(dep_path))
    if dep_src_path.exists() and str(dep_src_path) not in sys.path:
        sys.path.insert(0, str(dep_src_path))

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
    from orion.execution.risk.manager import RiskManager

    def _create(config=None):
        return RiskManager(config=config)

    return _create


# --- 4. Automatic marker application by directory ---
# Audit found ~94% of tests carried no marker, making `-m unit/integration/e2e`
# filters nearly useless. This hook auto-applies a marker based on the test's
# directory so the filters become meaningful, without anyone hand-tagging 1600+
# tests.
#
# Mapping (per the marker semantics in docs/testing-guide.md):
#   - tests/unit/**        -> unit        (pure logic, no I/O)
#   - tests/e2e/**         -> e2e         (full pipeline vs real TimescaleDB)
#   - tests/integration/** -> integration (DB wiring, mocked third-parties)
#   - tests/contracts/**   -> integration (cross-system contract tests)
#   - everything else under tests/ (the component dirs: execution, ingestion,
#     processing, jobs, agents, shared, connectors, ml, labeler, core, storage,
#     api, clients, enrichment, rag) -> integration
#
# The component dirs map to `integration` rather than `unit` because every test
# runs against the autouse in-memory SQLite DB fixture (setup_test_db above) and
# exercises real component wiring / mocked third-parties — which is exactly the
# `integration` definition in the testing guide, not the I/O-free `unit` one.
#
# Rules:
#   - Skip any item that already carries an explicit unit/integration/e2e/slow
#     marker (explicit markers win; never override or duplicate).
#   - Never apply `slow` — that would change the default `-m "not slow"` run and
#     the selected-test count must stay identical.
_MARKER_NAMES = ("unit", "integration", "e2e", "slow")
_TESTS_ROOT = Path(__file__).resolve().parent


def pytest_collection_modifyitems(config, items):
    for item in items:
        # Respect any explicit marker already on the item/module.
        if any(item.get_closest_marker(name) for name in _MARKER_NAMES):
            continue

        try:
            rel = Path(item.fspath).resolve().relative_to(_TESTS_ROOT)
        except ValueError:
            continue
        parts = rel.parts
        if not parts:
            continue
        top = parts[0]

        if top == "unit":
            marker = "unit"
        elif top == "e2e":
            marker = "e2e"
        else:
            # integration/, contracts/, all component dirs, and any loose files
            marker = "integration"

        item.add_marker(getattr(pytest.mark, marker))
