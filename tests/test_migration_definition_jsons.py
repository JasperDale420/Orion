"""Test that migration definition_json entries are valid SolverConfig objects."""

import sys
from pathlib import Path

import pytest

# Import migration data
sys.path.insert(0, str(Path(__file__).parent.parent / "alembic" / "versions"))

from orion.core.solver_schema import SolverConfig


def test_definition_jsons_have_version_id():
    """Test that all definition_json entries in migration have version_id field."""
    try:
        # Import the migration module
        import importlib

        migration = importlib.import_module("0026_seed_initial_solvers")
    except ImportError as e:
        pytest.skip(f"Could not import migration module: {e}")

    # Get the definition_jsons dict
    definition_jsons = getattr(migration, "_DEFINITION_JSONS", None)
    assert definition_jsons is not None, "Migration must have _DEFINITION_JSONS"

    # Test each definition_json can be parsed as SolverConfig
    errors = []
    for solver_id, defn_json in definition_jsons.items():
        # Check version_id field exists
        assert "version_id" in defn_json, f"{solver_id} missing version_id in definition_json"

        # Test that it can be parsed as SolverConfig (this is what SolverRouter does)
        try:
            config = SolverConfig(**defn_json)
            assert config.version_id == solver_id, f"{solver_id} version_id mismatch"
        except Exception as e:
            errors.append(f"{solver_id}: {e}")

    if errors:
        pytest.fail("Failed to parse definition_json as SolverConfig:\n" + "\n".join(errors))


def test_configs_have_version_id():
    """Test that all config entries in migration have version_id field."""
    try:
        import importlib

        migration = importlib.import_module("0026_seed_initial_solvers")
    except ImportError as e:
        pytest.skip(f"Could not import migration module: {e}")

    configs = getattr(migration, "_CONFIGS", None)
    assert configs is not None, "Migration must have _CONFIGS"

    for solver_id, config in configs.items():
        assert "version_id" in config, f"{solver_id} missing version_id in config"
        assert config["version_id"] == solver_id, f"{solver_id} version_id mismatch"


def test_configs_and_definitions_match():
    """Test that config and definition_json have same solver IDs."""
    try:
        import importlib

        migration = importlib.import_module("0026_seed_initial_solvers")
    except ImportError as e:
        pytest.skip(f"Could not import migration module: {e}")

    configs = getattr(migration, "_CONFIGS", {})
    definition_jsons = getattr(migration, "_DEFINITION_JSONS", {})

    config_ids = set(configs.keys())
    defn_ids = set(definition_jsons.keys())

    assert config_ids == defn_ids, "Mismatch between config and definition_json solver IDs"
