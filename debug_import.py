import sys

print("Starting import...", flush=True)

# Mocking db to see if that helps
from unittest.mock import MagicMock

mock_db = MagicMock()
sys.modules["orion.storage.db"] = mock_db

try:
    from orion.execution.execution_engine import ExecutionEngine

    print("ExecutionEngine imported successfully.", flush=True)
except ImportError as e:
    print(f"ImportError: {e}", flush=True)
except Exception as e:
    print(f"Exception during import: {e}", flush=True)

print("Done.", flush=True)
