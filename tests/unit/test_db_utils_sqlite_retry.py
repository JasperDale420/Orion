from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

import orion.shared.db_utils as db_utils
import pytest
from sqlalchemy.exc import OperationalError


class _FakeSession:
    def __init__(self, dialect_name: str) -> None:
        self._dialect_name = dialect_name
        self.commit_calls = 0
        self.rollback_calls = 0

    def get_bind(self) -> Any:
        return SimpleNamespace(dialect=SimpleNamespace(name=self._dialect_name))

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1


class _FakeSessionContext:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def _build_session_factory(dialect_name: str) -> tuple[Callable[[], _FakeSessionContext], list[_FakeSession]]:
    sessions: list[_FakeSession] = []

    def _factory() -> _FakeSessionContext:
        session = _FakeSession(dialect_name=dialect_name)
        sessions.append(session)
        return _FakeSessionContext(session)

    return _factory, sessions


@pytest.mark.asyncio
async def test_db_transaction_retries_sqlite_lock_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    factory, sessions = _build_session_factory("sqlite")
    monkeypatch.setattr(db_utils, "async_session_factory", factory)
    monkeypatch.setattr(db_utils, "SQLITE_LOCK_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(db_utils, "SQLITE_LOCK_RETRY_BASE_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(db_utils, "SQLITE_LOCK_RETRY_MAX_DELAY_SECONDS", 0.05)

    sleep_delays: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr(db_utils.asyncio, "sleep", _fake_sleep)

    attempts = {"count": 0}

    async def _operation(_session: Any) -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise OperationalError("SELECT 1", {}, Exception("database is locked"))
        return "ok"

    result = await db_utils.db_transaction(_operation)

    assert result == "ok"
    assert attempts["count"] == 3
    assert len(sessions) == 3
    assert [session.rollback_calls for session in sessions] == [1, 1, 0]
    assert [session.commit_calls for session in sessions] == [0, 0, 1]
    assert sleep_delays == [0.01, 0.02]


@pytest.mark.asyncio
async def test_db_transaction_does_not_retry_non_lock_operational_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, sessions = _build_session_factory("sqlite")
    monkeypatch.setattr(db_utils, "async_session_factory", factory)
    monkeypatch.setattr(db_utils, "SQLITE_LOCK_RETRY_ATTEMPTS", 3)

    sleep_delays: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr(db_utils.asyncio, "sleep", _fake_sleep)

    async def _operation(_session: Any) -> str:
        raise OperationalError("SELECT 1", {}, Exception("no such table: foo"))

    with pytest.raises(OperationalError):
        await db_utils.db_transaction(_operation)

    assert len(sessions) == 1
    assert sessions[0].rollback_calls == 1
    assert sleep_delays == []


@pytest.mark.asyncio
async def test_db_transaction_does_not_retry_lock_for_non_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    factory, sessions = _build_session_factory("postgresql")
    monkeypatch.setattr(db_utils, "async_session_factory", factory)
    monkeypatch.setattr(db_utils, "SQLITE_LOCK_RETRY_ATTEMPTS", 3)

    sleep_delays: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr(db_utils.asyncio, "sleep", _fake_sleep)

    async def _operation(_session: Any) -> str:
        raise OperationalError("SELECT 1", {}, Exception("database is locked"))

    with pytest.raises(OperationalError):
        await db_utils.db_transaction(_operation)

    assert len(sessions) == 1
    assert sessions[0].rollback_calls == 1
    assert sleep_delays == []


@pytest.mark.asyncio
async def test_db_transaction_raises_after_retry_budget_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    factory, sessions = _build_session_factory("sqlite")
    monkeypatch.setattr(db_utils, "async_session_factory", factory)
    monkeypatch.setattr(db_utils, "SQLITE_LOCK_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(db_utils, "SQLITE_LOCK_RETRY_BASE_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(db_utils, "SQLITE_LOCK_RETRY_MAX_DELAY_SECONDS", 0.05)

    sleep_delays: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    monkeypatch.setattr(db_utils.asyncio, "sleep", _fake_sleep)

    async def _operation(_session: Any) -> str:
        raise OperationalError("SELECT 1", {}, Exception("database table is locked"))

    with pytest.raises(OperationalError):
        await db_utils.db_transaction(_operation)

    assert len(sessions) == 3
    assert [session.rollback_calls for session in sessions] == [1, 1, 1]
    assert sleep_delays == [0.01, 0.02]
