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
