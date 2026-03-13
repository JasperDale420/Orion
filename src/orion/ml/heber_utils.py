"""Shared utilities for Heber-sourced ML training data."""

import hashlib
from typing import Any

from orion.shared.dataframe_utils import first_existing_column  # noqa: F401 — re-export


def generate_deterministic_event_ids(frame: Any) -> Any:
    """Create deterministic 16-char hex IDs from ticker + timestamp + strike.

    Used as a fallback when Heber Gold data lacks a natural ``event_id`` column.
    Shared between pattern_miner and exit_classifier training pipelines.
    """
    import pandas as pd

    parts: list[Any] = []
    for col in ("ticker", "symbol", "underlying_symbol"):
        if col in frame.columns:
            parts.append(frame[col].astype(str))
            break
    else:
        parts.append(pd.Series("unknown", index=frame.index))

    for col in ("entry_ts", "ts_event", "ts_available", "alert_time"):
        if col in frame.columns:
            parts.append(frame[col].astype(str))
            break
    else:
        parts.append(pd.Series("no_ts", index=frame.index))

    if "strike" in frame.columns:
        parts.append(frame["strike"].astype(str))

    combined = parts[0].str.cat(parts[1:], sep="|")
    # List comprehension is 2-3x faster than .apply(lambda) for SHA-256
    values = combined.tolist()
    return pd.Series(
        [hashlib.sha256(v.encode()).hexdigest()[:16] for v in values],
        index=combined.index,
    )


def coerce_dataframe(payload: Any) -> Any:
    """Coerce various payload types into a pandas DataFrame."""
    import pandas as pd

    if payload is None:
        return pd.DataFrame()
    if isinstance(payload, pd.DataFrame):
        return payload
    if isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    if isinstance(payload, dict):
        return pd.DataFrame([payload])
    return pd.DataFrame()
