"""Stale unfilled ENTRY orders must be cancelled at the broker, not left to
expire at the close.

2026-06-09: Orion's mid-priced DAY limit entries (EWY x7 @14:02, EWY x1 @15:45,
XHB x1 @16:42) sat unfilled all session — reserving shared day-trading buying
power and risking a late fill on a stale signal — then expired at the close.
`poll_fills` updated their status but never cancelled them.

The `orders` table holds ENTRIES only (closes persist to exit_decisions and
bracket SL/TP legs are never persisted there), so querying it scopes the
auto-cancel sweep to genuine buy-to-open entries — a buy-to-close on a short
position can never be cancelled by this path.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///:memory:")

from sqlalchemy import select

from orion.execution.execution_engine import ExecutionEngine
from orion.storage.db import async_session_factory, init_db
from orion.storage.models_execution import OrderRecord


def _engine() -> ExecutionEngine:
    """Bare engine; the DB query is stubbed so these target cancel logic only."""
    ee = ExecutionEngine.__new__(ExecutionEngine)
    ee._remove_pending_order_compat = AsyncMock()
    return ee


# ── cancel logic (DB query stubbed) ──────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancels_each_stale_entry_and_drops_pending() -> None:
    ee = _engine()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[
            {"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "EWY"},
            {"broker_order_id": "b-2", "client_order_id": "orion_b", "ticker": "XHB"},
        ]
    )
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value={})  # success

    n = await ee._cancel_stale_entry_orders(client)

    assert n == 2
    assert client.cancel_order.await_count == 2
    client.cancel_order.assert_any_await("b-1")
    client.cancel_order.assert_any_await("b-2")
    assert ee._remove_pending_order_compat.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_rejection_is_not_counted_and_keeps_pending() -> None:
    """Gateway surfaces failures as {"error": ...} (not exceptions). A rejected
    cancel must not be counted and must not drop the pending-order reservation."""
    ee = _engine()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "EWY"}]
    )
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value={"error": "order not cancelable"})

    n = await ee._cancel_stale_entry_orders(client)

    assert n == 0
    ee._remove_pending_order_compat.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_exception_is_swallowed() -> None:
    ee = _engine()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "EWY"}]
    )
    client = AsyncMock()
    client.cancel_order = AsyncMock(side_effect=RuntimeError("gateway down"))

    n = await ee._cancel_stale_entry_orders(client)  # must not raise

    assert n == 0
    ee._remove_pending_order_compat.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_stale_orders_is_noop() -> None:
    ee = _engine()
    ee._fetch_stale_entry_orders = AsyncMock(return_value=[])
    client = AsyncMock()

    n = await ee._cancel_stale_entry_orders(client)

    assert n == 0
    client.cancel_order.assert_not_awaited()


# ── query logic (real test DB) ───────────────────────────────────────────────


async def _wipe_orders() -> None:
    async with async_session_factory() as session:
        for row in (await session.execute(select(OrderRecord))).scalars().all():
            await session.delete(row)
        await session.commit()


async def _add_order(**kw: object) -> None:
    async with async_session_factory() as session:
        session.add(OrderRecord(**kw))
        await session.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fetch_selects_only_stale_unfilled_orion_entries() -> None:
    await init_db()
    await _wipe_orders()

    ttl = ExecutionEngine._STALE_ENTRY_ORDER_TTL_SECONDS
    old = datetime.now(UTC) - timedelta(seconds=ttl + 60)
    fresh = datetime.now(UTC) - timedelta(seconds=5)

    # SHOULD match: stale, unfilled, orion entry that reached the broker.
    await _add_order(
        id="o1",
        ticker="EWY",
        side="buy",
        qty=7,
        client_order_id="orion_stale",
        broker_order_id="b-stale",
        status="new",
        created_at_utc=old,
    )
    # too fresh — still within its fill window.
    await _add_order(
        id="o2",
        ticker="XHB",
        side="buy",
        qty=1,
        client_order_id="orion_fresh",
        broker_order_id="b-fresh",
        status="new",
        created_at_utc=fresh,
    )
    # already filled.
    await _add_order(
        id="o3",
        ticker="SPY",
        side="buy",
        qty=1,
        client_order_id="orion_filled",
        broker_order_id="b-filled",
        status="filled",
        created_at_utc=old,
    )
    # already terminal.
    await _add_order(
        id="o4",
        ticker="DIA",
        side="buy",
        qty=1,
        client_order_id="orion_exp",
        broker_order_id="b-exp",
        status="expired",
        created_at_utc=old,
    )
    # not orion-attributed (sibling system on the shared account).
    await _add_order(
        id="o5",
        ticker="AAPL",
        side="buy",
        qty=1,
        client_order_id="cerb_x",
        broker_order_id="b-cerb",
        status="new",
        created_at_utc=old,
        system="cerberus",
    )
    # orion entry that never reached the broker (no broker_order_id to cancel).
    await _add_order(
        id="o6",
        ticker="MU",
        side="buy",
        qty=1,
        client_order_id="orion_nobroker",
        broker_order_id=None,
        status="new",
        created_at_utc=old,
    )
    # already filling — must never be cancelled out from under a partial fill.
    await _add_order(
        id="o7",
        ticker="QQQ",
        side="buy",
        qty=2,
        client_order_id="orion_partial",
        broker_order_id="b-partial",
        status="partially_filled",
        created_at_utc=old,
    )
    # crash-window sentinel (pre-broker tracking row) — excluded by status.
    await _add_order(
        id="o8",
        ticker="IWM",
        side="buy",
        qty=1,
        client_order_id="orion_pending_submit",
        broker_order_id="b-pendsub",
        status="PENDING_SUBMIT",
        created_at_utc=old,
    )
    # REJECTED close row written by persist_exit_order_rejection — proves the
    # `orders` table is not strictly entries-only, yet this is still excluded
    # (terminal status; broker_order_id is null).
    await _add_order(
        id="o9",
        ticker="NVDA",
        side="sell",
        qty=1,
        client_order_id="orion_rejected_close",
        broker_order_id=None,
        status="REJECTED",
        created_at_utc=old,
    )

    ee = ExecutionEngine.__new__(ExecutionEngine)
    rows = await ee._fetch_stale_entry_orders()

    coids = {r["client_order_id"] for r in rows}
    assert coids == {"orion_stale"}
    match = next(r for r in rows if r["client_order_id"] == "orion_stale")
    assert match["broker_order_id"] == "b-stale"
    assert match["ticker"] == "EWY"
