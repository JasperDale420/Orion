"""The b3_pnl_attribution migration creates the three RB.3 tables on top of B1.

Companion to test_baseline_parity.py / test_liveness_migration.py. The
attribution tables ship as a SEPARATE post-baseline migration chained off
``b1_flow_push_parity`` (created by the parallel B1 work), so this test loads
that migration in isolation (stubbed alembic.op recorder) and asserts it creates
exactly the three tables — and that its down_revision chains off b1 so
``alembic heads`` stays single.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.unit

MIGRATION_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "b3_pnl_attribution.py"

EXPECTED_TABLES = {"pnl_reconciliation", "solver_pnl_attribution", "rule_pnl_attribution"}


def _load_migration():
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
        spec = importlib.util.spec_from_file_location("_orion_b3_migration", MIGRATION_PATH)
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


def test_migration_creates_three_attribution_tables():
    _module, created, dropped = _load_migration()
    assert set(created) == EXPECTED_TABLES
    assert set(dropped) == EXPECTED_TABLES


def test_migration_chains_off_b1():
    module, _created, _dropped = _load_migration()
    assert module.revision == "b3_pnl_attribution"
    assert module.down_revision == "b1_flow_push_parity"


def test_metadata_includes_attribution_tables():
    from orion.storage import models_attribution  # noqa: F401
    from orion.storage.models import Base

    assert EXPECTED_TABLES.issubset(set(Base.metadata.tables.keys()))
