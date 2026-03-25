import logging
import math
from datetime import UTC, date, datetime
from typing import Any

import dateutil.parser
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def make_json_safe(value: Any) -> Any:
    """Convert non-JSON-serializable types to JSON-safe Python primitives.

    Handles pandas Timestamps, numpy scalars, datetime objects, NaN/Inf floats,
    and recurses into dicts and lists.  Intended as a universal sanitizer for
    any dict that will be stored in a JSON/JSONB column.
    """
    if value is None:
        return None

    # pandas NaT — must be checked before Timestamp (NaT is a Timestamp subclass)
    if isinstance(value, type(pd.NaT)) or value is pd.NaT:
        return None

    # pandas Timestamp
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.isoformat()

    # numpy datetime64
    if isinstance(value, np.datetime64):
        if np.isnat(value):
            return None
        return pd.Timestamp(value).isoformat()

    # stdlib datetime / date
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()

    # float — guard against NaN / Inf
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value

    # numpy numeric scalars
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    if isinstance(value, np.bool_):
        return bool(value)

    # numpy array
    if isinstance(value, np.ndarray):
        return [make_json_safe(item) for item in value.tolist()]

    # Containers — recurse
    if isinstance(value, dict):
        return {k: make_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [make_json_safe(v) for v in value]

    # Everything else (str, int, bool, etc.) passes through
    return value


def parse_timestamptz(ts_input: str | int | float | None, *, strict: bool = False) -> datetime:
    """
    Parses a timestamp input into a timezone-aware UTC datetime object.
    Supports ISO strings, int/float timestamps (seconds or ms).
    Defaults to current UTC time if input is None or parsing fails.
    """
    now = datetime.now(UTC)

    if ts_input is None:
        return now

    try:
        # If float/int, assume unix timestamp
        if isinstance(ts_input, (int, float)):
            # Heuristic: if > 3bb, it's probably milliseconds
            if ts_input > 3000000000:
                ts_input = ts_input / 1000.0
            return datetime.fromtimestamp(ts_input, tz=UTC)

        # If string, try ISO parsing
        if isinstance(ts_input, str):
            try:
                # Optimized path for ISO format
                dt = datetime.fromisoformat(ts_input.replace("Z", "+00:00"))
            except ValueError:
                # Fallback to slower, more flexible parser
                dt = dateutil.parser.parse(ts_input)

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            else:
                dt = dt.astimezone(UTC)
            return dt

    except Exception as e:
        if strict:
            raise ValueError(f"Failed to parse timestamp '{ts_input}': {e}") from e
        logger.warning(f"Failed to parse timestamp '{ts_input}': {e}. Defaulting to now.")

    return now


def ensure_utc(dt: datetime | None) -> datetime | None:
    """
    Ensure datetime has UTC timezone.

    Args:
        dt: Datetime that may or may not have timezone info

    Returns:
        Datetime with UTC timezone, or None if input is None
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def parse_occ_symbol(symbol: str | None) -> dict[str, str | float | None]:
    """
    Parse an OCC option symbol into its components.

    OCC format: [Underlying][YYMMDD][C/P][Strike*1000]
    Example: SLV251231P00064000 -> SLV, 2025-12-31, P, 64.00

    Args:
        symbol: OCC option symbol string

    Returns:
        Dict with keys: underlying, expiry, put_call, strike
        Empty dict if parsing fails
    """
    import re

    if not symbol or not isinstance(symbol, str):
        return {}

    # OCC symbols: 1-6 char underlying + 6 digit date + C/P + 8 digit strike
    # Pattern: letters (underlying) + 6 digits (YYMMDD) + C or P + 8 digits (strike)
    pattern = r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$"
    match = re.match(pattern, symbol.upper())

    if not match:
        return {}

    underlying, date_str, put_call, strike_str = match.groups()

    try:
        # Parse date: YYMMDD
        year = 2000 + int(date_str[:2])
        month = int(date_str[2:4])
        day = int(date_str[4:6])
        expiry = f"{year}-{month:02d}-{day:02d}"

        # Parse strike: last 8 digits / 1000
        strike = int(strike_str) / 1000.0

        return {
            "underlying": underlying,
            "expiry": expiry,
            "put_call": "C" if put_call == "C" else "P",
            "strike": strike,
        }
    except (ValueError, IndexError):
        return {}
