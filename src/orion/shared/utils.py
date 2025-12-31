import logging
from datetime import datetime, timezone

import dateutil.parser

logger = logging.getLogger(__name__)


def parse_timestamptz(ts_input: str | int | float | None, *, strict: bool = False) -> datetime:
    """
    Parses a timestamp input into a timezone-aware UTC datetime object.
    Supports ISO strings, int/float timestamps (seconds or ms).
    Defaults to current UTC time if input is None or parsing fails.
    """
    now = datetime.now(timezone.utc)

    if ts_input is None:
        return now

    try:
        # If float/int, assume unix timestamp
        if isinstance(ts_input, (int, float)):
            # Heuristic: if > 3bb, it's probably milliseconds
            if ts_input > 3000000000:
                ts_input = ts_input / 1000.0
            return datetime.fromtimestamp(ts_input, tz=timezone.utc)

        # If string, try ISO parsing
        if isinstance(ts_input, str):
            dt = dateutil.parser.parse(ts_input)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
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
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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
