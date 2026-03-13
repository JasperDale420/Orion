from datetime import UTC, datetime

import pytest

from orion.core.market_schedule import MarketSchedule


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
