import hashlib
import json
from collections.abc import Mapping
from typing import Any


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def deterministic_solver_id(base_solver_id: str, edit_ops: Mapping[str, Any], prefix: str = "v") -> str:
    """
    Generates a deterministic solver/version identifier derived from the base solver id + edit ops.
    This avoids UUID/random IDs (PRDv2: deterministic config; no stub ids).
    """
    raw = f"{base_solver_id}|{stable_json_dumps(edit_ops)}"
    return f"{prefix}_{sha256_hex(raw)[:16]}"
