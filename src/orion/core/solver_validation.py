from __future__ import annotations

import os
from typing import Any, Dict

from orion.core.errors import ErrorCode
from orion.core.solver_dsl import SolverDSL


def validate_solver_definition_json(definition_json: Dict[str, Any]) -> SolverDSL:
    """
    Validates a solver DSL definition_json per PRD Addendum FR 5.1.1.
    Returns the parsed DSL object (typed).
    """
    return SolverDSL.model_validate(definition_json)


def ensure_solver_definition_json(
    config_blob: Dict[str, Any], definition_json: Dict[str, Any] | None
) -> Dict[str, Any]:
    """
    Ensures we have a PRD DSL definition_json. If missing, derive from legacy config.
    Always returns a JSON-serializable dict.
    """
    if isinstance(definition_json, dict) and definition_json:
        dsl = validate_solver_definition_json(definition_json)
        return dsl.model_dump(mode="json")

    dsl = SolverDSL.from_legacy_config(config_blob)
    return dsl.model_dump(mode="json")


def solver_dsl_error_extra(exc: Exception) -> dict:
    return {"error_code": ErrorCode.SOLVER_DSL_VALIDATION_FAILED.value, "error": str(exc), "cwd": os.getcwd()}
