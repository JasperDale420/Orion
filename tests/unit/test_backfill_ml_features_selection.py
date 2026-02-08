from __future__ import annotations

from typing import Any

import pytest

from orion.jobs import backfill_ml_features


@pytest.mark.asyncio
async def test_get_records_to_backfill_uses_deterministic_ordering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeResult:
        def fetchall(self) -> list[Any]:
            return []

    class _FakeSession:
        async def execute(self, stmt: Any, params: dict[str, Any]) -> _FakeResult:
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _FakeResult()

    async def _fake_db_query(fn):
        return await fn(_FakeSession())

    monkeypatch.setattr(backfill_ml_features, "db_query", _fake_db_query)

    records = await backfill_ml_features.get_records_to_backfill(limit=25)

    assert records == []
    assert "ORDER BY p.entry_ts ASC, p.event_id ASC" in captured["sql"]
    assert captured["params"]["limit"] == 25
