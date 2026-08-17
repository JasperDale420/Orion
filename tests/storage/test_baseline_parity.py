"""Static parity guard for the squashed alembic baseline.

Asserts that the single baseline migration creates exactly the set of tables
declared in ``Base.metadata``. This catches the most common squash-rot failure:
a new model is added (registered on ``Base.metadata`` and created by
``init_db()``'s ``create_all``) but the baseline migration is never regenerated,
so a fresh migration-driven database silently lacks the table.

No live database is required — the test stubs ``alembic.op`` with a recorder and
invokes the baseline's ``upgrade()`` to capture which tables it would create.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.unit

BASELINE_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "baseline_2026_06_11_baseline_full_schema.py"
)


def _load_baseline_with_recorder() -> tuple[set[str], dict[str, set[str]]]:
    """Import the baseline module against a stubbed ``alembic.op`` and return the
    set of table names its ``upgrade()`` creates."""
    created: set[str] = set()
    created_columns: dict[str, set[str]] = {}

    def _create_table(name: str, *args: Any, **kwargs: Any) -> None:
        created.add(name)
        # Capture column names so the parity check covers more than table
        # presence (adversarial-review finding: name-only parity false-passes
        # on missing/renamed columns).
        cols = {a.name for a in args if hasattr(a, "name") and hasattr(a, "type")}
        created_columns[name] = cols

    recorder = SimpleNamespace(
        create_table=_create_table,
        create_index=lambda *a, **k: None,
        drop_table=lambda *a, **k: None,
        drop_index=lambda *a, **k: None,
        execute=lambda *a, **k: None,
        f=lambda name: name,
    )

    # Stub the alembic.op module the baseline imports (`from alembic import op`).
    import alembic

    original_op = getattr(alembic, "op", None)
    alembic.op = recorder  # type: ignore[attr-defined]
    sys.modules["alembic.op"] = recorder  # type: ignore[assignment]
    try:
        spec = importlib.util.spec_from_file_location("_orion_baseline_under_test", BASELINE_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.upgrade()
    finally:
        if original_op is not None:
            alembic.op = original_op  # type: ignore[attr-defined]
        sys.modules.pop("alembic.op", None)

    return created, created_columns


def test_baseline_creates_every_metadata_table() -> None:
    # Import all model modules so Base.metadata is complete (same set init_db
    # imports). This mirrors alembic/env.py.
    from orion.storage import (  # noqa: F401
        models,
        models_attribution,
        models_audit,
        models_dlq,
        models_execution,
        models_flow_parity,
        models_gold,
        models_liveness,
        models_ml,
        models_risk,
        models_signals,
        models_silver,
        models_solvers,
        models_trade_journal,
    )
    from orion.storage.models import Base

    metadata_tables = set(Base.metadata.tables.keys())
    baseline_tables, baseline_columns = _load_baseline_with_recorder()

    # Tables intentionally shipped by a SEPARATE post-baseline migration rather
    # than folded into the squashed baseline. Each entry must have its own
    # migration test (e.g. test_liveness_migration.py for service_liveness).
    # They are excluded here so the baseline guard does not demand they be added
    # to the baseline (which would defeat the post-baseline-migration design).
    POST_BASELINE_TABLES = {
        "service_liveness",
        "flow_push_parity",
        "pnl_reconciliation",
        "solver_pnl_attribution",
        "rule_pnl_attribution",
    }

    # Models deleted from the codebase whose tables intentionally REMAIN in
    # existing databases (data preserved, no longer managed by the ORM).
    # vector_documents: RAG stack removed 2026-07 with the LLM solver-evolution
    # machinery; the table holds historical embeddings and is left in place.
    REMOVED_MODEL_TABLES = {"vector_documents"}

    missing_from_baseline = metadata_tables - baseline_tables - POST_BASELINE_TABLES
    extra_in_baseline = baseline_tables - metadata_tables - REMOVED_MODEL_TABLES

    assert not missing_from_baseline, (
        "Models exist that the alembic baseline does not create — regenerate the "
        f"baseline. Missing: {sorted(missing_from_baseline)}"
    )
    assert not extra_in_baseline, (
        "The alembic baseline creates tables not in Base.metadata — a model was "
        f"removed without updating the baseline. Extra: {sorted(extra_in_baseline)}"
    )

    # Columns added by post-baseline migrations to EXISTING tables. These are
    # intentionally absent from the squashed baseline; each must have its own
    # migration (see b4_journal_exit_legs.py). Register here to prevent the
    # column-drift check from demanding they be folded into the baseline.
    POST_BASELINE_COLUMN_ADDITIONS: dict[str, set[str]] = {
        "trade_journal_entries": {
            "exit_filled_qty",
            "exit_filled_avg_price",
            "exit_filled_at_utc",
            "exit_broker_order_id",
        },
        # b5_risk_accounting_version.py — provenance for the risk_state money
        # columns (NULL = written before the option contract-multiplier fix).
        # b6_risk_daily_loss_date.py — trading date the persisted daily loss
        # belongs to (NULL = written before the column existed).
        "risk_state": {"accounting_version", "daily_loss_date"},
    }

    # Column-level parity (adversarial-review finding: table-name-only parity
    # false-passes on added/removed/renamed columns). Compare column-name sets
    # per table; types/indexes/FKs are covered by the live autogen-empty check
    # documented in archive/2026-06-11_alembic-pre-baseline/README.md.
    column_drift: dict[str, str] = {}
    for table_name in sorted(metadata_tables - POST_BASELINE_TABLES):
        model_cols = set(Base.metadata.tables[table_name].columns.keys())
        post_baseline_cols = POST_BASELINE_COLUMN_ADDITIONS.get(table_name, set())
        migration_cols = baseline_columns.get(table_name, set())
        effective_model_cols = model_cols - post_baseline_cols
        if effective_model_cols != migration_cols:
            missing = sorted(effective_model_cols - migration_cols)
            extra = sorted(migration_cols - effective_model_cols)
            column_drift[table_name] = f"missing={missing} extra={extra}"
    assert not column_drift, f"Baseline column drift vs Base.metadata — regenerate the baseline: {column_drift}"
