from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


class SchemaValidationError(ValueError):
    """Raised when runtime payload columns do not match table schema."""

    def __init__(
        self,
        message: str,
        *,
        unknown_columns: Iterable[str] | None = None,
        missing_columns: Iterable[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.unknown_columns = tuple(sorted(set(unknown_columns or [])))
        self.missing_columns = tuple(sorted(set(missing_columns or [])))


def resolve_insert_columns(
    row: Mapping[str, Any],
    allowed_columns: set[str],
    required_columns: Iterable[str] | None = None,
) -> list[str]:
    """Resolve ordered insert columns and reject unknown/missing keys."""
    required = set(required_columns or [])
    missing = [key for key in required if key not in row]
    if missing:
        raise SchemaValidationError(
            "Missing required columns in row payload",
            missing_columns=missing,
        )

    unknown = [key for key in row if key not in allowed_columns]
    if unknown:
        raise SchemaValidationError(
            "Row payload contains unknown columns",
            unknown_columns=unknown,
        )

    resolved = [key for key in row if key in allowed_columns]
    if not resolved:
        raise SchemaValidationError("Row payload does not contain insertable columns")
    return resolved
