"""Guard so the test-DB teardown can never ``drop_all`` a real database.

The autouse ``setup_test_db`` fixture in ``conftest.py`` drops every table on
teardown to isolate tests. Several e2e tests reconfigure ``db.engine`` to the
REAL TimescaleDB (``db.configure_db(REAL_DB_URL)``) and are expected to restore
the in-memory binding before that teardown runs. If one fails to, an unguarded
``drop_all`` would WIPE that database — the cause of the 2026-06-30 production
solver-table wipe (every solver dropped; ``bronze``/``silver`` refilled from
live ingestion within minutes, but ``solvers`` did not until the next execution
restart, so Orion SKIPped every candidate during market hours). The teardown
drops ONLY when this returns ``True``.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine


def is_in_memory_test_engine(engine: AsyncEngine | None) -> bool:
    """True only for the in-memory SQLite engine the suite binds for isolation.

    Anything else — a file-backed SQLite, or (critically) the real TimescaleDB an
    e2e test pointed at — returns False, so the conftest teardown will not drop it.
    """
    if engine is None:
        return False
    return engine.url.get_backend_name() == "sqlite" and ":memory:" in str(engine.url)
