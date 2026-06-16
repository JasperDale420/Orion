"""The service_liveness migration creates the table, on top of the baseline.

Companion to test_baseline_parity.py. That test proves the baseline creates the
full metadata set; the liveness table ships as a SEPARATE post-baseline
migration (down_revision=baseline_2026_06_11), so this test loads that migration
in isolation (stubbed alembic.op recorder, the established pattern) and asserts
it creates exactly the service_liveness table — and that its down_revision
chains off the baseline so `alembic heads` stays single.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.unit

MIGRATION_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "a1_service_liveness.py"


def _load_migration():
    """Import the migration module against a stubbed alembic.op recorder.

    Returns (module, created_tables, dropped_tables)."""
    created: list[str] = []
    dropped: list[str] = []

    recorder = SimpleNamespace(
        create_table=lambda name, *a, **k: created.append(name),
        drop_table=lambda name, *a, **k: dropped.append(name),
        create_index=lambda *a, **k: None,
        drop_index=lambda *a, **k: None,
        execute=lambda *a, **k: None,
        f=lambda name: name,
    )

    import alembic

    original_op = getattr(alembic, "op", None)
    alembic.op = recorder  # type: ignore[attr-defined]
    sys.modules["alembic.op"] = recorder  # type: ignore[assignment]
    try:
        spec = importlib.util.spec_from_file_location("_orion_liveness_migration", MIGRATION_PATH)
        assert spec is not None and spec.loader is not None
        module: Any = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.upgrade()
        module.downgrade()
    finally:
        if original_op is not None:
            alembic.op = original_op  # type: ignore[attr-defined]
        sys.modules.pop("alembic.op", None)

    return module, created, dropped


def test_migration_creates_service_liveness_table():
    _module, created, dropped = _load_migration()
    assert created == ["service_liveness"]
    assert dropped == ["service_liveness"]


def test_migration_chains_off_baseline():
    module, _created, _dropped = _load_migration()
    assert module.revision == "a1_service_liveness"
    assert module.down_revision == "baseline_2026_06_11"


def test_metadata_includes_service_liveness():
    """The model is registered on Base.metadata (so init_db/create_all builds it
    and the baseline-parity guard would catch a missing baseline entry)."""
    from orion.storage import models_liveness  # noqa: F401
    from orion.storage.models import Base

    assert "service_liveness" in Base.metadata.tables
