from datetime import UTC, datetime

import pytest

from orion.core.market_schedule import MarketSchedule, resolve_session_close


@pytest.fixture
def schedule():
    return MarketSchedule()


def test_singleton_behavior(schedule):
    s2 = MarketSchedule()
    assert schedule is s2


def test_market_is_open_basic(schedule):
    # Mocking exchange_calendars interactions would be ideal,
    # but for integration we can test known open/close times if the calendar is loaded.
    if not schedule.calendar:
        pytest.skip("Calendar not loaded")

    # A known open time (e.g., a generic Wednesday at 10 AM ET)
    # Note: XNYS is UTC based in exchange_calendars usually?
    # Actually exchange_calendars XNYS open is 9:30 AM America/New_York

    # Let's use a recent known trading day: Wed Dec 27 2023
    # 10 AM ET = 15:00 UTC
    open_time = datetime(2023, 12, 27, 15, 0, 0, tzinfo=UTC)
    assert schedule.is_market_open(open_time) is True

    # A known closed time (e.g., Sunday)
    closed_time = datetime(2023, 12, 24, 15, 0, 0, tzinfo=UTC)
    assert schedule.is_market_open(closed_time) is False


def test_get_open_close(schedule):
    if not schedule.calendar:
        pytest.skip("Calendar not loaded")

    # Wed Dec 27 2023
    ts = datetime(2023, 12, 27, 12, 0, 0, tzinfo=UTC)
    open_t, close_t = schedule.get_open_close(ts)

    assert open_t is not None
    assert close_t is not None
    # 9:30 AM ET = 14:30 UTC
    assert open_t.hour == 14
    assert open_t.minute == 30

    # Sunday
    ts_sun = datetime(2023, 12, 24, 12, 0, 0, tzinfo=UTC)
    o, c = schedule.get_open_close(ts_sun)
    assert o is None
    assert c is None


def test_seconds_until_open(schedule):
    if not schedule.calendar:
        pytest.skip("Calendar not loaded")

    # Time: 9:29 AM ET -> 1 min until open
    # Dec 27 2023 14:29 UTC
    ts = datetime(2023, 12, 27, 14, 29, 0, tzinfo=UTC)

    seconds = schedule.seconds_until_open(ts)
    assert 59 <= seconds <= 61

    # During market: 0
    ts_open = datetime(2023, 12, 27, 15, 0, 0, tzinfo=UTC)
    assert schedule.seconds_until_open(ts_open) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# is_market_open_for_options — Phase 2 of exit-pipeline RCA
# ---------------------------------------------------------------------------


def test_options_market_open_during_rth(schedule):
    """Tuesday 2026-05-19 14:30 UTC = 10:30 AM ET (during RTH)."""
    if not schedule.calendar:
        pytest.skip("Calendar not loaded")
    in_rth = datetime(2026, 5, 19, 14, 30, 0, tzinfo=UTC)
    assert schedule.is_market_open_for_options(in_rth) is True


def test_options_market_closed_pre_market(schedule):
    """Tuesday 2026-05-19 12:00 UTC = 8:00 AM ET (pre-market).

    Equity *might* count this as open if the calendar config includes
    pre-market, but Alpaca rejects options orders here with 42210000.
    """
    if not schedule.calendar:
        pytest.skip("Calendar not loaded")
    pre = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)
    assert schedule.is_market_open_for_options(pre) is False


def test_options_market_closed_after_hours(schedule):
    """Tuesday 2026-05-19 22:00 UTC = 6:00 PM ET (after-hours)."""
    if not schedule.calendar:
        pytest.skip("Calendar not loaded")
    after = datetime(2026, 5, 19, 22, 0, 0, tzinfo=UTC)
    assert schedule.is_market_open_for_options(after) is False


def test_options_market_closed_weekend(schedule):
    """Saturday 2026-05-23 14:30 UTC — no trading at all."""
    if not schedule.calendar:
        pytest.skip("Calendar not loaded")
    sat = datetime(2026, 5, 23, 14, 30, 0, tzinfo=UTC)
    assert schedule.is_market_open_for_options(sat) is False


def test_options_market_open_at_open_bell(schedule):
    """Exactly at 9:30 AM ET = 14:30 UTC, options trading is open."""
    if not schedule.calendar:
        pytest.skip("Calendar not loaded")
    open_bell = datetime(2026, 5, 19, 14, 30, 0, tzinfo=UTC)
    assert schedule.is_market_open_for_options(open_bell) is True


def test_options_market_closed_at_close_bell(schedule):
    """Exactly at 4:00 PM ET = 20:00 UTC, options trading is closed
    (the [open, close) half-open interval is what Alpaca enforces)."""
    if not schedule.calendar:
        pytest.skip("Calendar not loaded")
    close_bell = datetime(2026, 5, 19, 20, 0, 0, tzinfo=UTC)
    assert schedule.is_market_open_for_options(close_bell) is False


# --- resolve_session_close --------------------------------------------------
#
# The 0DTE flatten deadline and the 0DTE entry wind-down both derive their
# time-of-day windows from this, so an early close must be visible to it and
# a dead calendar must not read as "16:00 as usual".


def _et(hour: int, minute: int) -> datetime:
    from zoneinfo import ZoneInfo

    return datetime(2026, 11, 27, hour, minute, tzinfo=ZoneInfo("America/New_York"))


def test_resolve_session_close_returns_calendar_close(monkeypatch):
    close = _et(13, 0)  # 2026-11-27 is the day after Thanksgiving: a half day

    class _Schedule:
        def get_todays_close(self, _ts):
            return close

    monkeypatch.setattr("orion.core.market_schedule.MarketSchedule", _Schedule)
    assert resolve_session_close(_et(12, 0)) == close


def test_resolve_session_close_none_outside_session(monkeypatch):
    """No session in progress is not a failure — callers keep their fixed cutoff."""

    class _Schedule:
        def get_todays_close(self, _ts):
            return None

    monkeypatch.setattr("orion.core.market_schedule.MarketSchedule", _Schedule)
    assert resolve_session_close(_et(18, 0)) is None


def test_resolve_session_close_fails_safe_when_calendar_unavailable(monkeypatch):
    """Calendar down: assume the earliest close NYSE runs (13:00 ET) rather
    than leaving the only 0DTE expiry protection on a 16:00 assumption."""

    class _Schedule:
        def get_todays_close(self, _ts):
            raise RuntimeError("Cannot determine today's close without calendar")

    monkeypatch.setattr("orion.core.market_schedule.MarketSchedule", _Schedule)
    assert resolve_session_close(_et(12, 0)) == _et(13, 0)


def test_resolve_session_close_treats_naive_close_as_utc(monkeypatch):
    class _Schedule:
        def get_todays_close(self, _ts):
            return _et(13, 0).astimezone(UTC).replace(tzinfo=None)

    monkeypatch.setattr("orion.core.market_schedule.MarketSchedule", _Schedule)
    assert resolve_session_close(_et(12, 0)) == _et(13, 0)


def test_resolve_session_close_reads_a_real_early_close(schedule):
    """End-to-end against the bundled XNYS calendar, no mocks: the day after
    Thanksgiving 2026 closes at 13:00 ET, not 16:00."""
    if not schedule.calendar:
        pytest.skip("Calendar not loaded")
    close = resolve_session_close(_et(12, 0))
    assert close is not None
    from zoneinfo import ZoneInfo

    assert close.astimezone(ZoneInfo("America/New_York")).hour == 13
