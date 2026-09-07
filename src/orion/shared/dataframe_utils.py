"""Shared DataFrame utilities used across Orion modules."""

from typing import Any

import pandas as pd


def first_existing_column(frame: Any, candidates: list[str] | tuple[str, ...]) -> str | None:
    """Return the first column name from *candidates* that exists in *frame*.columns."""
    for col in candidates:
        if col in frame.columns:
            return col
    return None


def coerce_ticker_column(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure *df* has a ``ticker`` column, deriving it from ``symbol`` or
    ``instrument_key`` if needed. Returns *df* unchanged if none of those
    columns are present.
    """
    if "ticker" in df.columns:
        return df
    if "symbol" in df.columns:
        return df.assign(ticker=df["symbol"].astype(str).str.upper())
    if "instrument_key" in df.columns:
        return df.assign(ticker=df["instrument_key"].astype(str).str.split(":").str[-1].str.upper())
    return df
