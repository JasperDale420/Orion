from datetime import UTC, datetime

import pytest

from orion.shared.utils import parse_timestamptz


class TestParseTimestamptz:
    def test_iso_string_z(self):
        ts = "2023-10-26T12:00:00Z"
        dt = parse_timestamptz(ts)
        assert dt == datetime(2023, 10, 26, 12, 0, 0, tzinfo=UTC)

    def test_iso_string_offset(self):
        ts = "2023-10-26T12:00:00+00:00"
        dt = parse_timestamptz(ts)
        assert dt == datetime(2023, 10, 26, 12, 0, 0, tzinfo=UTC)

    def test_iso_string_microseconds(self):
        ts = "2023-10-26T12:00:00.123456Z"
        dt = parse_timestamptz(ts)
        assert dt == datetime(2023, 10, 26, 12, 0, 0, 123456, tzinfo=UTC)

    def test_naive_string(self):
        ts = "2023-10-26T12:00:00"
        dt = parse_timestamptz(ts)
        assert dt == datetime(2023, 10, 26, 12, 0, 0, tzinfo=UTC)

    def test_non_iso_string(self):
        ts = "Oct 26, 2023 12:00:00"
        dt = parse_timestamptz(ts)
        assert dt == datetime(2023, 10, 26, 12, 0, 0, tzinfo=UTC)

    def test_float_seconds(self):
        ts = 1698321600.0  # 2023-10-26 12:00:00 UTC
        dt = parse_timestamptz(ts)
        assert dt == datetime(2023, 10, 26, 12, 0, 0, tzinfo=UTC)

    def test_int_milliseconds(self):
        ts = 1698321600000  # 2023-10-26 12:00:00 UTC
        dt = parse_timestamptz(ts)
        assert dt == datetime(2023, 10, 26, 12, 0, 0, tzinfo=UTC)

    def test_none(self):
        dt = parse_timestamptz(None)
        assert isinstance(dt, datetime)
        assert dt.tzinfo == UTC
        # Should be close to now
        assert (datetime.now(UTC) - dt).total_seconds() < 1

    def test_invalid_string_strict(self):
        with pytest.raises(ValueError):
            parse_timestamptz("invalid", strict=True)

    def test_invalid_string_non_strict(self):
        dt = parse_timestamptz("invalid", strict=False)
        assert isinstance(dt, datetime)
        assert dt.tzinfo == UTC
