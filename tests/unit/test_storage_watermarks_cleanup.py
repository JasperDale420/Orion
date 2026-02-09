from __future__ import annotations

from typing import Any

import pytest

from orion.storage import watermarks


class _ScalarResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _DummyResult:
    pass


class _FakeSession:
    def __init__(self, count: int) -> None:
        self.count = count
        self.calls: list[Any] = []

    async def execute(self, stmt: Any) -> Any:
        self.calls.append(stmt)
        if len(self.calls) == 1:
            return _ScalarResult(self.count)
        return _DummyResult()


@pytest.mark.asyncio
async def test_delete_watermarks_returns_zero_without_keys() -> None:
    session = _FakeSession(count=999)

    deleted = await watermarks.delete_watermarks(session, ())

    assert deleted == 0
    assert session.calls == []


@pytest.mark.asyncio
async def test_delete_watermarks_skips_delete_when_no_rows_match() -> None:
    session = _FakeSession(count=0)

    deleted = await watermarks.delete_watermarks(session, ("k1", "k2"))

    assert deleted == 0
    assert len(session.calls) == 1
    assert "SELECT count(*)" in str(session.calls[0])


@pytest.mark.asyncio
async def test_delete_watermarks_deletes_matching_rows_once() -> None:
    session = _FakeSession(count=2)

    deleted = await watermarks.delete_watermarks(session, ("k1", "k2", "k1"))

    assert deleted == 2
    assert len(session.calls) == 2
    assert "SELECT count(*)" in str(session.calls[0])
    assert "DELETE FROM ingest_watermarks" in str(session.calls[1])
