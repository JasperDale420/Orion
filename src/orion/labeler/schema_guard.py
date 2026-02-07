from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Optional, Set

from sqlalchemy import text


class SchemaValidationError(ValueError):
    """Raised when runtime payload columns do not match table schema."""

    def __init__(
        self,
        message: str,
        *,
        unknown_columns: Optional[Iterable[str]] = None,
        missing_columns: Optional[Iterable[str]] = None,
    ) -> None:
        super().__init__(message)
        self.unknown_columns = tuple(sorted(set(unknown_columns or [])))
        self.missing_columns = tuple(sorted(set(missing_columns or [])))


async def fetch_table_columns(session: Any, table_name: str, schema: str = "public") -> Set[str]:
    """Read column names for a table from information_schema."""
    stmt = text(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = :schema
          AND table_name = :table_name
        """
    )
    result = await session.execute(stmt, {"schema": schema, "table_name": table_name})
    columns = {row[0] for row in result.fetchall()}
    if not columns:
        raise SchemaValidationError(
            f"No columns found for table {schema}.{table_name}",
            missing_columns=[table_name],
        )
    return columns


def resolve_insert_columns(
    row: Mapping[str, Any],
    allowed_columns: Set[str],
    required_columns: Optional[Iterable[str]] = None,
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
