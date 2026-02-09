from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

import orion.main_price_target_labeler as labeler


@pytest.mark.asyncio
async def test_get_entry_signals_prefers_heber_flow_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    old_ts = now - timedelta(hours=3)

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "event_id": "evt-1",
                        "ticker": "AAPL",
                        "flow_ts_utc": old_ts,
                        "option_chain": "AAPL250117C00200000",
                        "option_price": 1.25,
                        "expiry": "2026-02-20",
                        "premium_usd": 60000.0,
                        "aggressor": "ASK",
                        "put_call": "CALL",
                        "is_sweep": "true",
                    }
                ]
            )

    async def _fake_get_labeled_event_ids(_event_ids: list[str]) -> set[str]:
        return set()

    async def _fail_sql_fallback(_limit: int):
        raise AssertionError("SQL fallback should not be used when Heber returns valid entries")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_labeled_price_target_event_ids", _fake_get_labeled_event_ids, raising=False)
    monkeypatch.setattr(labeler, "_get_entry_signals_sql", _fail_sql_fallback, raising=False)

    entries = await labeler.get_entry_signals(limit=5)

    assert len(entries) == 1
    assert entries[0].event_id == "evt-1"
    assert entries[0].ticker == "AAPL"


@pytest.mark.asyncio
async def test_get_entry_signals_falls_back_to_sql_when_heber_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    async def _fake_get_entry_signals_sql(limit: int):
        assert limit == 3
        return [SimpleNamespace(event_id="sql-1")]

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_entry_signals_sql", _fake_get_entry_signals_sql, raising=False)

    entries = await labeler.get_entry_signals(limit=3)

    assert len(entries) == 1
    assert entries[0].event_id == "sql-1"


@pytest.mark.asyncio
async def test_get_subsequent_prices_prefers_heber_flow_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "option_chain": "AAPL250117C00200000",
                        "flow_ts_utc": entry_ts + timedelta(minutes=5),
                        "option_price": 1.5,
                    },
                    {
                        "option_chain": "AAPL250117C00200000",
                        "flow_ts_utc": entry_ts + timedelta(minutes=10),
                        "option_price": 1.8,
                    },
                ]
            )

    async def _fail_sql_fallback(_option_chain: str, _entry_ts: datetime):
        raise AssertionError("SQL fallback should not be used when Heber flow provides prices")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_subsequent_prices_sql", _fail_sql_fallback, raising=False)

    prices = await labeler.get_subsequent_prices("AAPL250117C00200000", entry_ts)

    assert prices == [
        {"price": 1.5, "ts": entry_ts + timedelta(minutes=5)},
        {"price": 1.8, "ts": entry_ts + timedelta(minutes=10)},
    ]


@pytest.mark.asyncio
async def test_get_subsequent_prices_falls_back_to_sql_when_heber_missing_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame([{"unexpected": "shape"}])

    async def _fake_sql_fallback(option_chain: str, ts: datetime):
        assert option_chain == "AAPL250117C00200000"
        assert ts == entry_ts
        return [{"price": 2.0, "ts": entry_ts + timedelta(minutes=1)}]

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_subsequent_prices_sql", _fake_sql_fallback, raising=False)

    prices = await labeler.get_subsequent_prices("AAPL250117C00200000", entry_ts)

    assert prices == [{"price": 2.0, "ts": entry_ts + timedelta(minutes=1)}]
