from __future__ import annotations

import pytest

from orion.jobs import validate_features


@pytest.mark.asyncio
async def test_run_sanity_checks_uses_minutes_to_close_bound_from_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeReader:
        def read_gold_features(self, dataset: str, asof_time, symbols=None):
            _ = (asof_time, symbols)
            if dataset == "labels_alert_barriers":
                return [
                    {"alert_id": "a1", "underlying": "AAPL", "ts_event": "2026-02-11T15:00:00Z"},
                    {"alert_id": "a2", "underlying": "MSFT", "ts_event": "2026-02-11T15:00:00Z"},
                ]
            if dataset == "meta_label_features":
                return [
                    {
                        "alert_id": "a1",
                        "delta": 0.3,
                        "gamma": 0.1,
                        "iv_rank": 50.0,
                        "minutes_to_close": 390,
                        "hour_of_day": 14,
                        "darkpool_1h": 1.0,
                        "rvol_1h": 1.0,
                    },
                    {
                        "alert_id": "a2",
                        "delta": 0.3,
                        "gamma": 0.1,
                        "iv_rank": 50.0,
                        "minutes_to_close": 391,
                        "hour_of_day": 14,
                        "darkpool_1h": 1.0,
                        "rvol_1h": 1.0,
                    },
                ]
            raise AssertionError(f"unexpected dataset: {dataset}")

    monkeypatch.setattr(validate_features, "get_heber_reader", lambda: _FakeReader())

    async def _fail_db_query(_fn):
        raise AssertionError("local db_query should not be used")

    monkeypatch.setattr(validate_features, "db_query", _fail_db_query, raising=False)

    results = await validate_features.run_sanity_checks()

    assert results["failed"] == 1
    assert any("minutes_to_close in [0, 390]" in issue for issue in results["issues"])


@pytest.mark.asyncio
async def test_run_sanity_checks_flags_unready_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeReader:
        def read_gold_features(self, dataset: str, asof_time, symbols=None):
            _ = (asof_time, symbols)
            if dataset == "labels_alert_barriers":
                return [
                    {"alert_id": "a1", "underlying": "AAPL", "ts_event": "2026-02-11T15:00:00Z"},
                    {"alert_id": "a2", "underlying": "MSFT", "ts_event": "2026-02-11T15:00:00Z"},
                ]
            if dataset == "meta_label_features":
                return [
                    {
                        "alert_id": "a1",
                        "delta": 0.3,
                        "gamma": 0.1,
                        "iv_rank": 50.0,
                        "minutes_to_close": 120,
                        "hour_of_day": 14,
                        "darkpool_1h": 1.0,
                        "rvol_1h": 1.0,
                    }
                ]
            raise AssertionError(f"unexpected dataset: {dataset}")

    monkeypatch.setattr(validate_features, "get_heber_reader", lambda: _FakeReader())

    async def _fail_db_query(_fn):
        raise AssertionError("local db_query should not be used")

    monkeypatch.setattr(validate_features, "db_query", _fail_db_query, raising=False)

    results = await validate_features.run_sanity_checks()

    assert results["failed"] == 1
    assert any("ml_ready = false" in issue for issue in results["issues"])
