import sys

print("Starting import...", flush=True)

# Mocking db to see if that helps
from unittest.mock import MagicMock

mock_db = MagicMock()
sys.modules["orion.storage.db"] = mock_db

import importlib.util

spec = importlib.util.find_spec("orion.execution.execution_engine")
if spec:
    try:
        from orion.execution.execution_engine import ExecutionEngine  # noqa: F401

        print("ExecutionEngine imported successfully.", flush=True)
    except Exception as e:
        print(f"Exception during import: {e}", flush=True)
else:
    print("ExecutionEngine module not found.", flush=True)

print("Done.", flush=True)
