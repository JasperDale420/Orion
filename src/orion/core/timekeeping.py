from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

_ET = ZoneInfo("America/New_York")


def last_closed_trading_date(ts_utc: datetime | None = None, *, calendar_name: str = "XNYS") -> date:
    """Return the US equity trading date of the most-recently-CLOSED session.

    Computes the latest NYSE (``XNYS``) session whose regular-hours close is at
    or before ``ts_utc`` (defaults to now, UTC). This is the correct target for
    a post-close EOD run: a weekday trigger that fires shortly after midnight UTC
    (i.e. the prior evening in ET) resolves to that just-closed ET session, and a
    weekend/holiday trigger walks back to the last open session. Using the
    exchange calendar — not UTC-today — means a Saturday 01:05 UTC trigger
    reconciles Friday's session, not an empty "today".
    """
    if ts_utc is None:
        ts_utc = datetime.now(UTC)
    if ts_utc.tzinfo is None:
        raise ValueError("ts_utc must be timezone-aware UTC datetime")

    cal = xcals.get_calendar(calendar_name)
    ts = pd.Timestamp(ts_utc)
    # Start from the ET calendar day of the trigger, then walk back to the most
    # recent session whose regular close is at/before the trigger instant.
    et_day = pd.Timestamp(ts_utc.astimezone(_ET).date())
    session = cal.date_to_session(et_day, direction="previous")
    while cal.session_close(session) > ts:
        session = cal.previous_session(session)
    return session.date()


def closed_sessions_between(after: date, through: date, *, calendar_name: str = "XNYS") -> list[date]:
    """XNYS sessions strictly after ``after``, up to and including ``through``.

    Lets the EOD close-of-books advance its cursor one session at a time instead
    of jumping straight to the newest one: if ingestion is down Thursday night
    and resumes after Friday's close, Thursday must still be reconciled rather
    than silently skipped. Returns [] when ``through <= after``.
    """
    if through <= after:
        return []
    cal = xcals.get_calendar(calendar_name)
    sessions = cal.sessions_in_range(pd.Timestamp(after), pd.Timestamp(through))
    return [s.date() for s in sessions if s.date() > after]


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
