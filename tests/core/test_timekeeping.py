"""`last_closed_trading_date` resolves the session the EOD close-of-books targets.

Getting this wrong makes `reconcile_pnl` filter fills to the wrong day and
measure an empty session. These cases were previously covered only indirectly,
through tests of the (now removed) `hour == 1 UTC` EOD trigger; they belong to
the function that actually does the work.
"""

from datetime import UTC, date, datetime

import pytest

from orion.core.timekeeping import closed_sessions_between, last_closed_trading_date, sessions_forward

pytestmark = pytest.mark.unit


def test_sessions_between_excludes_the_cursor_and_includes_the_target():
    # Wed 2026-08-05 .. Mon 2026-08-10 — skips Sat/Sun.
    assert closed_sessions_between(date(2026, 8, 5), date(2026, 8, 10)) == [
        date(2026, 8, 6),
        date(2026, 8, 7),
        date(2026, 8, 10),
    ]


def test_sessions_between_is_empty_when_already_current():
    assert closed_sessions_between(date(2026, 8, 10), date(2026, 8, 10)) == []


def test_sessions_between_is_empty_when_cursor_is_ahead():
    """A cursor ahead of the target must not produce a backwards walk."""
    assert closed_sessions_between(date(2026, 8, 11), date(2026, 8, 10)) == []


def test_sessions_between_skips_a_market_holiday():
    # 2026-07-03 is the observed Independence Day holiday (Jul 4 = Saturday).
    walked = closed_sessions_between(date(2026, 7, 1), date(2026, 7, 7))
    assert date(2026, 7, 3) not in walked
    assert date(2026, 7, 2) in walked and date(2026, 7, 6) in walked


def test_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        last_closed_trading_date(datetime(2026, 6, 9, 1, 5))


def test_post_close_edt_resolves_the_just_closed_session():
    """Tue 01:05 UTC == Mon 21:05 EDT, after Monday's close."""
    assert last_closed_trading_date(datetime(2026, 6, 9, 1, 5, tzinfo=UTC)) == date(2026, 6, 8)


def test_post_close_est_resolves_the_just_closed_session():
    """Wed 01:05 UTC == Tue 20:05 EST, after Tuesday's close."""
    assert last_closed_trading_date(datetime(2026, 1, 14, 1, 5, tzinfo=UTC)) == date(2026, 1, 13)


def test_weekend_walks_back_to_friday():
    """Sat 01:05 UTC == Fri 21:05 ET — Friday's session, not an empty 'today'."""
    assert last_closed_trading_date(datetime(2026, 6, 6, 1, 5, tzinfo=UTC)) == date(2026, 6, 5)


def test_sunday_still_resolves_friday():
    assert last_closed_trading_date(datetime(2026, 6, 7, 18, 0, tzinfo=UTC)) == date(2026, 6, 5)


def test_intraday_before_close_resolves_the_previous_session():
    """Mid-session, the most recently CLOSED session is still the prior day."""
    # 2026-06-09 17:00 UTC == 13:00 EDT, Tuesday's session still open.
    assert last_closed_trading_date(datetime(2026, 6, 9, 17, 0, tzinfo=UTC)) == date(2026, 6, 8)


def test_just_after_close_resolves_the_same_day():
    """2026-06-09 20:05 UTC == 16:05 EDT, just past Tuesday's close."""
    assert last_closed_trading_date(datetime(2026, 6, 9, 20, 5, tzinfo=UTC)) == date(2026, 6, 9)


# `sessions_forward` — windows expressed in trading sessions, used to time-box
# the per-bucket entry halts.


def test_sessions_forward_skips_the_weekend():
    """Fri 2026-08-14 + 1 session is Monday, not Saturday."""
    assert sessions_forward(date(2026, 8, 14), 1) == date(2026, 8, 17)


def test_sessions_forward_counts_sessions_not_calendar_days():
    """Ten sessions from a Friday lands two trading weeks out."""
    assert sessions_forward(date(2026, 8, 14), 10) == date(2026, 8, 28)


def test_sessions_forward_from_a_non_session_start():
    """A Saturday start has no session to exclude; the first one is Monday."""
    assert sessions_forward(date(2026, 8, 15), 1) == date(2026, 8, 17)


def test_sessions_forward_skips_a_holiday():
    """Thanksgiving 2026 (Thu 11-26) is not a session, so #2 is Mon 11-30."""
    assert sessions_forward(date(2026, 11, 25), 2) == date(2026, 11, 30)


def test_sessions_forward_rejects_a_non_positive_window():
    with pytest.raises(ValueError):
        sessions_forward(date(2026, 8, 14), 0)
