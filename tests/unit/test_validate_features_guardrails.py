from __future__ import annotations

from typing import Any

import pytest

from orion.jobs import validate_features


@pytest.mark.asyncio
async def test_run_sanity_checks_query_uses_consistent_minutes_to_close_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    class _FakeRow:
        _mapping = {
            "total": 10,
            "bad_delta": 0,
            "bad_gamma": 0,
            "bad_iv_rank": 0,
            "bad_mtc": 0,
            "bad_hour": 0,
            "bad_dp": 0,
            "bad_rvol": 0,
            "not_ready": 0,
        }

    class _FakeResult:
        def fetchone(self) -> _FakeRow:
            return _FakeRow()

    class _FakeSession:
        async def execute(self, stmt: Any) -> _FakeResult:
            captured["sql"] = str(stmt)
            return _FakeResult()

    async def _fake_db_query(fn):
        return await fn(_FakeSession())

    monkeypatch.setattr(validate_features, "db_query", _fake_db_query)

    await validate_features.run_sanity_checks()
    assert "minutes_to_close < 0 OR minutes_to_close > 390" in captured["sql"]


@pytest.mark.asyncio
async def test_run_sanity_checks_flags_unready_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_db_query(_fn):
        return {
            "total": 100,
            "bad_delta": 0,
            "bad_gamma": 0,
            "bad_iv_rank": 0,
            "bad_mtc": 0,
            "bad_hour": 0,
            "bad_dp": 0,
            "bad_rvol": 0,
            "not_ready": 5,
        }

    monkeypatch.setattr(validate_features, "db_query", _fake_db_query)

    results = await validate_features.run_sanity_checks()

    assert results["failed"] == 1
    assert any("ml_ready = false" in issue for issue in results["issues"])
