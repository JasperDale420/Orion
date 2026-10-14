"""Operator command that closes a stuck circuit breaker.

The previous ``scripts/reset_circuit_breaker.py`` imported ``psycopg2``, which
is not a project dependency, so the exact command the dead-man-watchdog alert
and the circuit-breaker runbook pointed operators at failed at import. This
covers its replacement, ``orion.jobs.reset_circuit_breaker``, which uses the
project's own async ORM path instead of a raw DB-API connection.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from orion.core.circuit_breaker import CircuitBreaker
from orion.jobs.reset_circuit_breaker import DEFAULT_REASON, main, run_reset
from orion.storage.db import async_session_factory
from orion.storage.models import SystemStatus

pytestmark = pytest.mark.unit


async def _seed_status(status: str, *, details: str = "prior details") -> None:
    async with async_session_factory() as session:
        session.add(
            SystemStatus(
                key=CircuitBreaker.KEY,
                status=status,
                details=details,
                last_updated_utc=datetime.now(UTC),
            )
        )
        await session.commit()


async def _read_status() -> SystemStatus | None:
    async with async_session_factory() as session:
        stmt = select(SystemStatus).where(SystemStatus.key == CircuitBreaker.KEY)
        return (await session.execute(stmt)).scalars().first()


# --- run_reset() behavior -----------------------------------------------


async def test_run_reset_closes_an_open_breaker_with_the_given_reason() -> None:
    await _seed_status("OPEN")

    await run_reset(reason="operator investigated and cleared")

    row = await _read_status()
    assert row is not None
    assert row.status == "CLOSED"
    assert row.details == "operator investigated and cleared"


async def test_run_reset_dry_run_reports_but_does_not_mutate() -> None:
    await _seed_status("OPEN", details="original details")

    await run_reset(reason="would reset", dry_run=True)

    row = await _read_status()
    assert row is not None
    assert row.status == "OPEN"
    assert row.details == "original details"


async def test_run_reset_is_a_no_op_when_already_closed() -> None:
    await _seed_status("CLOSED", details="already fine")

    await run_reset(reason="ignored")

    row = await _read_status()
    assert row is not None
    assert row.details == "already fine"


async def test_run_reset_is_a_no_op_when_no_row_exists() -> None:
    await run_reset(reason="ignored")

    row = await _read_status()
    assert row is None


# --- main() CLI ------------------------------------------------------------


def test_main_closes_an_open_breaker_end_to_end() -> None:
    asyncio.run(_seed_status("OPEN"))

    exit_code = main(["--reason", "operator via CLI"])

    assert exit_code == 0
    row = asyncio.run(_read_status())
    assert row is not None
    assert row.status == "CLOSED"
    assert row.details == "operator via CLI"


def test_main_uses_the_default_reason_when_reason_flag_is_omitted() -> None:
    asyncio.run(_seed_status("OPEN"))

    exit_code = main([])

    assert exit_code == 0
    row = asyncio.run(_read_status())
    assert row is not None
    assert row.details == DEFAULT_REASON


def test_main_parses_reason_and_dry_run_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run_reset(*, reason: str, dry_run: bool = False) -> dict[str, object]:
        captured["reason"] = reason
        captured["dry_run"] = dry_run
        return {"status": "OPEN"}

    monkeypatch.setattr("orion.jobs.reset_circuit_breaker.run_reset", fake_run_reset)

    exit_code = main(["--reason", "custom reason", "--dry-run"])

    assert exit_code == 0
    assert captured == {"reason": "custom reason", "dry_run": True}


def test_main_defaults_reason_when_not_given(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run_reset(*, reason: str, dry_run: bool = False) -> dict[str, object]:
        captured["reason"] = reason
        captured["dry_run"] = dry_run
        return {"status": "OPEN"}

    monkeypatch.setattr("orion.jobs.reset_circuit_breaker.run_reset", fake_run_reset)

    exit_code = main([])

    assert exit_code == 0
    assert captured == {"reason": DEFAULT_REASON, "dry_run": False}
