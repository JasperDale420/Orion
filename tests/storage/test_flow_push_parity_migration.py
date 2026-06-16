"""The flow_push_parity migration creates the table, chained off A1 liveness.

Companion to test_liveness_migration.py. The flow_push_parity table ships as a
post-baseline migration (down_revision=a1_service_liveness), so this test loads
that migration in isolation (stubbed alembic.op recorder, the established
pattern) and asserts it creates exactly the flow_push_parity table — and that
its revision id / down_revision keep the alembic chain single (B3 chains onto
this id).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.unit

MIGRATION_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "b1_flow_push_parity.py"


def _load_migration():
    """Import the migration against a stubbed alembic.op recorder.

    Returns (module, created_tables, created_columns)."""
    created: list[str] = []
    created_columns: dict[str, set[str]] = {}

    def _create_table(name: str, *args: Any, **kwargs: Any) -> None:
        created.append(name)
        created_columns[name] = {a.name for a in args if hasattr(a, "name") and hasattr(a, "type")}

    recorder = SimpleNamespace(
        create_table=_create_table,
        drop_table=lambda *a, **k: None,
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
        spec = importlib.util.spec_from_file_location("_orion_flow_parity_migration", MIGRATION_PATH)
        assert spec is not None and spec.loader is not None
        module: Any = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.upgrade()
        module.downgrade()
    finally:
        if original_op is not None:
            alembic.op = original_op  # type: ignore[attr-defined]
        sys.modules.pop("alembic.op", None)

    return module, created, created_columns


def test_migration_creates_flow_push_parity_table():
    _module, created, _columns = _load_migration()
    assert created == ["flow_push_parity"]


def test_migration_columns_match_model():
    """The migration's columns must match the model's (autogen parity)."""
    from orion.storage.models_flow_parity import FlowPushParity

    _module, _created, columns = _load_migration()
    model_cols = {c.name for c in FlowPushParity.__table__.columns}
    assert columns["flow_push_parity"] == model_cols


def test_migration_chains_off_liveness_keeping_head_single():
    module, _created, _columns = _load_migration()
    assert module.revision == "b1_flow_push_parity"
    assert module.down_revision == "a1_service_liveness"
