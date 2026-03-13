"""Shared DataFrame utilities used across Orion modules."""

from typing import Any


def first_existing_column(frame: Any, candidates: list[str] | tuple[str, ...]) -> str | None:
    """Return the first column name from *candidates* that exists in *frame*.columns."""
    for col in candidates:
        if col in frame.columns:
            return col
    return None
