from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
import pytest

import orion.main_price_target_labeler as labeler


@pytest.mark.asyncio
async def test_get_entry_signals_prefers_heber_flow_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
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

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "_get_labeled_price_target_event_ids", _fake_get_labeled_event_ids, raising=False)

    entries = await labeler.get_entry_signals(limit=5)

    assert len(entries) == 1
    assert entries[0].event_id == "evt-1"
    assert entries[0].ticker == "AAPL"


@pytest.mark.asyncio
async def test_get_entry_signals_returns_empty_when_heber_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    entries = await labeler.get_entry_signals(limit=3)

    assert entries == []


@pytest.mark.asyncio
async def test_get_subsequent_prices_prefers_heber_flow_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=UTC)

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

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    prices = await labeler.get_subsequent_prices("AAPL250117C00200000", entry_ts)

    assert prices == [
        {"price": 1.5, "ts": entry_ts + timedelta(minutes=5)},
        {"price": 1.8, "ts": entry_ts + timedelta(minutes=10)},
    ]


@pytest.mark.asyncio
async def test_get_subsequent_prices_returns_empty_when_heber_missing_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=UTC)

    class _FakeHeberReader:
        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame([{"unexpected": "shape"}])

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)

    prices = await labeler.get_subsequent_prices("AAPL250117C00200000", entry_ts)

    assert prices == []
