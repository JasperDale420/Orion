"""Durable per-bucket entry halts: session math, idempotent writes, expiry, CLI."""

import json
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from orion.core.timekeeping import last_closed_trading_date, sessions_forward
from orion.jobs import bucket_halt
from orion.jobs.bucket_halt import (
    HALT_STATUS,
    SET_BY_MEASUREMENT,
    SET_BY_OPERATOR,
    active_halts,
    get_active_halt,
    halt_key,
    list_halts,
    record_halt,
    release_expired_halts,
    remove_halt,
    reset_halt_cache,
    run_cli,
)
from orion.storage.db import async_session_factory, init_db
from orion.storage.models import SystemStatus

pytestmark = pytest.mark.asyncio

# 2026-08-14 is a Friday; no XNYS holiday falls between it and 2026-08-28.
FRIDAY = date(2026, 8, 14)
# A trading instant inside the halt window (Monday 2026-08-17, 14:00 UTC).
MONDAY_UTC = datetime(2026, 8, 17, 14, 0, tzinfo=UTC)


# ── Writing halts ────────────────────────────────────────────────────────


async def test_record_halt_writes_a_durable_row() -> None:
    await init_db()
    write = await record_halt("SWING", profit_factor=0.42, n_closed=63, now=MONDAY_UTC)

    assert write.outcome == "written"
    assert write.halt is not None
    assert write.halt.expires_after_session == sessions_forward(FRIDAY, 10)

    async with async_session_factory() as session:
        row = (
            (await session.execute(select(SystemStatus).where(SystemStatus.key == "bucket_halt:SWING")))
            .scalars()
            .first()
        )
        assert row is not None
        assert row.status == HALT_STATUS
        details = json.loads(row.details)
        assert details["pf"] == 0.42
        assert details["n"] == 63
        assert details["set_by"] == SET_BY_MEASUREMENT
        assert details["expires_after_session"] == write.halt.expires_after_session.isoformat()


async def test_record_halt_is_idempotent_and_never_extends() -> None:
    await init_db()
    first = await record_halt("SWING", profit_factor=0.42, n_closed=63, now=MONDAY_UTC)

    # A later nightly pass inside the window must leave the expiry alone.
    later = datetime(2026, 8, 20, 14, 0, tzinfo=UTC)
    second = await record_halt("SWING", profit_factor=0.30, n_closed=70, now=later)

    assert second.outcome == "already_halted"
    halts = await active_halts(now=later)
    assert halts["SWING"].expires_after_session == first.halt.expires_after_session
    assert halts["SWING"].profit_factor == 0.42


async def test_record_halt_never_overwrites_an_operator_halt() -> None:
    await init_db()
    await record_halt("SWING", profit_factor=None, n_closed=None, set_by=SET_BY_OPERATOR, now=MONDAY_UTC)

    write = await record_halt("SWING", profit_factor=0.1, n_closed=90, now=MONDAY_UTC)

    assert write.outcome == "operator_halt_present"
    halts = await active_halts(now=MONDAY_UTC)
    assert halts["SWING"].set_by == SET_BY_OPERATOR
    assert halts["SWING"].profit_factor is None


# ── Expiry ───────────────────────────────────────────────────────────────


async def test_halt_is_active_through_its_last_session_and_expires_after() -> None:
    await init_db()
    write = await record_halt("0DTE", profit_factor=0.5, n_closed=55, now=MONDAY_UTC)
    last = write.halt.expires_after_session  # 2026-08-28

    on_last = datetime(last.year, last.month, last.day, 17, 0, tzinfo=UTC)
    assert await get_active_halt("0DTE", now=on_last) is not None

    # 2026-08-31 (Monday) is the first trading date past the window.
    assert await get_active_halt("0DTE", now=datetime(2026, 8, 31, 14, 0, tzinfo=UTC)) is None


async def test_release_expired_halts_frees_measurement_rows_only() -> None:
    await init_db()
    await record_halt("SWING", profit_factor=0.4, n_closed=60, now=MONDAY_UTC)
    await record_halt("0DTE", profit_factor=None, n_closed=None, set_by=SET_BY_OPERATOR, now=MONDAY_UTC)

    after = datetime(2026, 9, 15, 14, 0, tzinfo=UTC)
    released = await release_expired_halts(now=after)

    assert [h.bucket for h in released] == ["SWING"]
    assert {h.bucket for h in await list_halts()} == {"0DTE"}


async def test_release_expired_halts_leaves_a_live_halt_alone() -> None:
    await init_db()
    await record_halt("SWING", profit_factor=0.4, n_closed=60, now=MONDAY_UTC)

    assert await release_expired_halts(now=datetime(2026, 8, 20, 14, 0, tzinfo=UTC)) == []
    assert {h.bucket for h in await list_halts()} == {"SWING"}


# ── Gate read: cache and fail-open ───────────────────────────────────────


async def test_get_active_halt_caches_within_the_ttl() -> None:
    await init_db()
    await record_halt("SWING", profit_factor=0.4, n_closed=60, now=MONDAY_UTC)
    assert await get_active_halt("SWING", now=MONDAY_UTC) is not None

    # Row deleted underneath a warm cache: the gate keeps halting until the
    # TTL lapses. Bounded staleness, not unbounded.
    await remove_halt("SWING")
    assert await get_active_halt("SWING", now=MONDAY_UTC) is not None

    reset_halt_cache()
    assert await get_active_halt("SWING", now=MONDAY_UTC) is None


async def test_get_active_halt_cache_expires_after_ttl(monkeypatch) -> None:
    await init_db()
    await record_halt("SWING", profit_factor=0.4, n_closed=60, now=MONDAY_UTC)
    assert await get_active_halt("SWING", now=MONDAY_UTC) is not None
    await remove_halt("SWING")

    later = bucket_halt._monotonic() + bucket_halt.CACHE_TTL_SECONDS + 1.0
    monkeypatch.setattr(bucket_halt, "_monotonic", lambda: later)
    assert await get_active_halt("SWING", now=MONDAY_UTC) is None


async def test_get_active_halt_fails_open_on_db_error_and_warns() -> None:
    await init_db()
    await record_halt("SWING", profit_factor=0.4, n_closed=60, now=MONDAY_UTC)
    reset_halt_cache()

    warned = MagicMock()
    with (
        patch.object(bucket_halt, "_load_halts", AsyncMock(side_effect=RuntimeError("db down"))),
        patch.object(bucket_halt.logger, "warning", warned),
    ):
        assert await get_active_halt("SWING", now=MONDAY_UTC) is None

    warned.assert_called_once()


async def test_unparseable_details_do_not_halt() -> None:
    """A corrupt row must fail toward trading, not latch a permanent halt."""
    await init_db()
    async with async_session_factory() as session:
        session.add(SystemStatus(key=halt_key("SWING"), status=HALT_STATUS, details="not json"))
        await session.commit()

    assert await get_active_halt("SWING", now=MONDAY_UTC) is None


# ── Operator CLI ─────────────────────────────────────────────────────────


async def test_cli_set_list_and_clear(capsys) -> None:
    await init_db()

    assert await run_cli(["--bucket", "swing", "--set", "--sessions", "3", "--reason", "manual"]) == 0
    halts = {h.bucket: h for h in await list_halts()}
    assert halts["SWING"].set_by == SET_BY_OPERATOR
    assert halts["SWING"].reason == "manual"
    assert halts["SWING"].expires_after_session == sessions_forward(last_closed_trading_date(), 3)

    capsys.readouterr()
    assert await run_cli(["--list"]) == 0
    assert "SWING" in capsys.readouterr().out

    assert await run_cli(["--bucket", "SWING", "--clear"]) == 0
    assert await list_halts() == []


async def test_cli_clear_requires_a_bucket() -> None:
    await init_db()
    with pytest.raises(SystemExit):
        await run_cli(["--clear"])
