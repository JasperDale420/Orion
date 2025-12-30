from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

_ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class SessionBands:
    pre_start: time = time(4, 0)
    reg_start: time = time(9, 30)
    reg_end: time = time(16, 0)
    post_end: time = time(20, 0)


def derive_trading_date_and_session(
    ts_utc: datetime, *, calendar_name: str = "XNYS", bands: SessionBands = SessionBands()
) -> tuple[date, str]:
    """
    PRD 5: All timestamps stored as UTC; derive `trading_date` (ET calendar day) and `session`.
    Session bands are configurable; defaults match PRD.
    """
    if ts_utc.tzinfo is None:
        raise ValueError("ts_utc must be timezone-aware UTC datetime")

    cal = xcals.get_calendar(calendar_name)
    ts_et = ts_utc.astimezone(_ET)
    trading_day_et = ts_et.date()

    if not cal.is_session(trading_day_et):
        return trading_day_et, "CLOSED"

    t = ts_et.timetz().replace(tzinfo=None)

    if bands.pre_start <= t < bands.reg_start:
        return trading_day_et, "PRE"
    if bands.reg_start <= t < bands.reg_end:
        return trading_day_et, "REG"
    if bands.reg_end <= t <= bands.post_end:
        return trading_day_et, "POST"

    return trading_day_et, "CLOSED"
