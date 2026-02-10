from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import pytest

import orion.main_price_target_labeler as labeler


@pytest.mark.asyncio
async def test_get_max_pain_distance_prefers_heber_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    expiry_dt = datetime(2026, 2, 20, 0, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_max_pain(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "date": entry_ts.date() - timedelta(days=1),
                        "expiry": expiry_dt.date(),
                        "distance_to_max_pain_pct": 2.5,
                    },
                    {
                        "date": entry_ts.date(),
                        "expiry": expiry_dt.date(),
                        "distance_to_max_pain_pct": 1.75,
                    },
                ]
            )

    async def _fail_db_query(_callback):
        raise AssertionError("db_query should not be used when Heber max pain is available")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "db_query", _fail_db_query, raising=False)

    distance = await labeler.get_max_pain_distance("AAPL", expiry_dt, entry_ts)
    assert distance == 1.75


@pytest.mark.asyncio
async def test_get_max_pain_distance_returns_none_when_heber_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    expiry_dt = datetime(2026, 2, 20, 0, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_max_pain(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    async def _fail_db_query(_callback):
        raise AssertionError("db_query should not be used for max pain fallback")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "db_query", _fail_db_query, raising=False)

    distance = await labeler.get_max_pain_distance("AAPL", expiry_dt, entry_ts)
    assert distance is None


@pytest.mark.asyncio
async def test_get_iv_at_offset_prefers_heber_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)
    target_ts = entry_ts + timedelta(hours=2)

    class _FakeHeberReader:
        def read_iv_rank(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {"ts_utc": target_ts - timedelta(minutes=10), "iv_rank": 41.0},
                    {"ts_utc": target_ts, "iv_rank": 44.5},
                ]
            )

    async def _fail_db_query(_callback):
        raise AssertionError("db_query should not be used when Heber iv_rank is available")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "db_query", _fail_db_query, raising=False)

    iv_rank = await labeler.get_iv_at_offset("AAPL", entry_ts, hours=2)
    assert iv_rank == 44.5


@pytest.mark.asyncio
async def test_get_iv_at_offset_falls_back_to_heber_flow_estimate_when_iv_rank_unusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_iv_rank(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame([{"unexpected": "shape"}])

        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {"flow_ts_utc": entry_ts - timedelta(days=3), "iv": 0.20},
                    {"flow_ts_utc": entry_ts - timedelta(days=2), "iv": 0.30},
                    {"flow_ts_utc": entry_ts - timedelta(days=1), "iv": 0.40},
                    {"flow_ts_utc": entry_ts - timedelta(minutes=30), "iv": 0.45},
                ]
            )

    async def _fail_db_query(_callback):
        raise AssertionError("db_query should not be used for IV rank fallback")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "db_query", _fail_db_query, raising=False)

    iv_rank = await labeler.get_iv_at_offset("AAPL", entry_ts, hours=0)
    assert iv_rank == 100.0


@pytest.mark.asyncio
async def test_get_iv_rank_at_entry_prefers_heber_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_iv_rank(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {"ts_utc": entry_ts - timedelta(hours=1), "iv_rank": 36.0},
                    {"ts_utc": entry_ts - timedelta(minutes=5), "iv_rank": 44.0},
                ]
            )

    async def _fail_db_query(_callback):
        raise AssertionError("db_query should not be used when Heber IV rank is available")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "db_query", _fail_db_query, raising=False)

    iv_rank = await labeler.get_iv_rank_at_entry("AAPL", entry_ts)
    assert iv_rank == 44.0


@pytest.mark.asyncio
async def test_get_iv_rank_at_entry_returns_none_when_heber_iv_rank_and_flow_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_iv_rank(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

    async def _fail_db_query(_callback):
        raise AssertionError("db_query should not be used when Heber data is unavailable")

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "db_query", _fail_db_query, raising=False)

    iv_rank = await labeler.get_iv_rank_at_entry("AAPL", entry_ts)
    assert iv_rank is None


@pytest.mark.asyncio
async def test_get_iv_rank_at_entry_estimates_from_heber_flow_when_iv_rank_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_ts = datetime(2026, 2, 9, 15, 0, tzinfo=timezone.utc)

    class _FakeHeberReader:
        def read_iv_rank(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame()

        def read_flow(self, **_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {"flow_ts_utc": entry_ts - timedelta(days=3), "iv": 0.20},
                    {"flow_ts_utc": entry_ts - timedelta(days=2), "iv": 0.30},
                    {"flow_ts_utc": entry_ts - timedelta(days=1), "iv": 0.40},
                    {"flow_ts_utc": entry_ts - timedelta(hours=1), "iv": 0.45},
                ]
            )

    async def _fake_db_query(_callback):
        return None

    monkeypatch.setattr(labeler, "_heber_reader", _FakeHeberReader(), raising=False)
    monkeypatch.setattr(labeler, "db_query", _fake_db_query, raising=False)

    iv_rank = await labeler.get_iv_rank_at_entry("AAPL", entry_ts)
    assert iv_rank == 100.0
