from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from orion.jobs import nightly_backfill


def test_session_run_time_uses_calendar_close_plus_delay(monkeypatch) -> None:
    close_ts = datetime(2026, 2, 9, 21, 0, tzinfo=timezone.utc)
    mock_schedule = MagicMock()
    mock_schedule.get_open_close.return_value = (close_ts - timedelta(hours=6), close_ts)
    monkeypatch.setattr(nightly_backfill, "_MARKET_SCHEDULE", mock_schedule)

    run_ts = nightly_backfill._session_run_time_utc(datetime(2026, 2, 9, 12, 0, tzinfo=timezone.utc))
    assert run_ts == close_ts + timedelta(minutes=nightly_backfill.BACKFILL_DELAY_MINUTES)


def test_get_next_run_time_uses_next_future_session(monkeypatch) -> None:
    fixed_now = datetime(2026, 2, 7, 22, 0, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(nightly_backfill, "datetime", FixedDateTime)

    def fake_session_run_time(dt: datetime):
        if dt.date() == fixed_now.date():
            return fixed_now - timedelta(minutes=10)
        if dt.date() == (fixed_now + timedelta(days=1)).date():
            return fixed_now + timedelta(hours=2)
        return None

    monkeypatch.setattr(nightly_backfill, "_session_run_time_utc", fake_session_run_time)
    next_run = nightly_backfill.get_next_run_time()
    assert next_run == fixed_now + timedelta(hours=2)
