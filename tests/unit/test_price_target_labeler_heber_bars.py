from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

import orion.main_price_target_labeler as labeler


@pytest.mark.asyncio
async def test_get_underlying_price_at_entry_prefers_heber_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=UTC)

    class _FakeHeberReader:
        def read_bars(self, **_kwargs):
            return pd.DataFrame(
                [
                    {"ts_event": entry_ts - timedelta(minutes=5), "close": 123.4},
                    {"ts_event": entry_ts + timedelta(minutes=1), "close": 999.0},
                ]
            )

    async def _fail_db_query(_callback):
        raise AssertionError("db_query should not be used when Heber bars provide a value")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "db_query", _fail_db_query, raising=False)

    result = await labeler.get_underlying_price_at_entry("AAPL", entry_ts)

    assert result == 123.4


@pytest.mark.asyncio
async def test_get_underlying_price_at_entry_returns_none_when_heber_has_no_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=UTC)

    class _FakeHeberReader:
        def read_bars(self, **_kwargs):
            return pd.DataFrame()

    async def _fail_db_query(_callback):
        raise AssertionError("db_query should not be used when Heber bar data is unavailable")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "db_query", _fail_db_query, raising=False)

    result = await labeler.get_underlying_price_at_entry("AAPL", entry_ts)

    assert result is None


@pytest.mark.asyncio
async def test_get_underlying_price_at_offset_uses_heber_before_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=UTC)
    target_ts = entry_ts + timedelta(hours=2)

    class _FakeHeberReader:
        def read_bars(self, **_kwargs):
            return pd.DataFrame(
                [
                    {"bar_start_ts": target_ts - timedelta(minutes=3), "close": 222.2},
                ]
            )

    async def _fail_db_query(_callback):
        raise AssertionError("db_query should not be used when Heber bars provide a value")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "db_query", _fail_db_query, raising=False)

    result = await labeler.get_underlying_price_at_offset("AAPL", entry_ts, hours=2)

    assert result == 222.2


@pytest.mark.asyncio
async def test_get_underlying_price_at_offset_returns_none_when_heber_has_no_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=UTC)

    class _FakeHeberReader:
        def read_bars(self, **_kwargs):
            return pd.DataFrame()

    async def _fail_db_query(_callback):
        raise AssertionError("db_query should not be used when Heber bar data is unavailable")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "db_query", _fail_db_query, raising=False)

    result = await labeler.get_underlying_price_at_offset("AAPL", entry_ts, hours=2)

    assert result is None
