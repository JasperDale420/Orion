from datetime import UTC, datetime

import pytest

from orion.shared.utils import parse_timestamptz


def test_parse_timestamptz_iso_offset():
    ts = "2023-10-27T10:00:00+00:00"
    dt = parse_timestamptz(ts)
    assert dt.year == 2023
    assert dt.month == 10
    assert dt.day == 27
    assert dt.hour == 10
    assert dt.minute == 0
    assert dt.second == 0
    assert dt.tzinfo == UTC


def test_parse_timestamptz_iso_z():
    ts = "2023-10-27T10:00:00Z"
    dt = parse_timestamptz(ts)
    assert dt.year == 2023
    assert dt.month == 10
    assert dt.day == 27
    assert dt.hour == 10
    assert dt.minute == 0
    assert dt.second == 0
    assert dt.tzinfo == UTC


def test_parse_timestamptz_naive():
    ts = "2023-10-27T10:00:00"
    dt = parse_timestamptz(ts)
    assert dt.year == 2023
    assert dt.month == 10
    assert dt.day == 27
    assert dt.hour == 10
    assert dt.minute == 0
    assert dt.second == 0
    assert dt.tzinfo == UTC


def test_parse_timestamptz_unix_float():
    ts = 1698400800.0  # 2023-10-27 10:00:00 UTC
    dt = parse_timestamptz(ts)
    assert dt.year == 2023
    assert dt.month == 10
    assert dt.day == 27
    assert dt.hour == 10
    assert dt.minute == 0
    assert dt.second == 0
    assert dt.tzinfo == UTC


def test_parse_timestamptz_unix_ms():
    ts = 1698400800000.0  # 2023-10-27 10:00:00 UTC in ms
    dt = parse_timestamptz(ts)
    assert dt.year == 2023
    assert dt.month == 10
    assert dt.day == 27
    assert dt.hour == 10
    assert dt.minute == 0
    assert dt.second == 0
    assert dt.tzinfo == UTC


def test_parse_timestamptz_none():
    dt = parse_timestamptz(None)
    assert isinstance(dt, datetime)
    assert dt.tzinfo == UTC
    # Should be close to now
    assert (datetime.now(UTC) - dt).total_seconds() < 5


def test_parse_timestamptz_invalid_strict():
    with pytest.raises(ValueError):
        parse_timestamptz("invalid-date", strict=True)


def test_parse_timestamptz_invalid_non_strict():
    dt = parse_timestamptz("invalid-date", strict=False)
    assert isinstance(dt, datetime)
    assert dt.tzinfo == UTC
    assert (datetime.now(UTC) - dt).total_seconds() < 5
