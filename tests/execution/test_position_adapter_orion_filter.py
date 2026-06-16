"""Tests for `GatewayPositionAdapter`'s orion-attribution filter.

The Alpaca paper account is shared by multiple Empire trading systems
(3Roses, Cerberus, Kairos, Orbit, WhaleHunter, Orion). Without this
filter, `PositionMonitor.sync_positions` would absorb every broker
position into Orion's tracker — including positions belonging to other
systems — and the exit pipeline would generate close attempts for
positions Orion doesn't own.

Observed live on 2026-05-21: position monitor's tracker held 30
ticker entries while `ExecutionEngine._sync_risk_from_gateway`
reported "open_positions=0 skipped_non_orion=38" — confirming the
adapter wasn't filtering. Resulted in 85 `missing_current_price` +
300+ Alpaca 403 errors per 15-min window.

Fix mirrors the orion-attribution pattern from
`ExecutionEngine._fetch_orion_tickers`: query the `orders` table for
distinct tickers with `client_order_id LIKE 'orion_%'`, then drop any
broker position whose symbol isn't in that set.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///:memory:")


@pytest.mark.asyncio
async def test_adapter_filters_to_orion_attributed_positions() -> None:
    """Broker returns 3 positions; only 1 has a matching orion-prefixed
    fill in the DB. Tracker should see only that 1."""
    from sqlalchemy import text

    from orion.execution.position_monitor import GatewayPositionAdapter
    from orion.storage.db import async_session_factory, init_db

    await init_db()

    # Plant an Orion-attributed FILL for AAPL and a non-orion fill
    # for MSFT. ABC has no fill at all. The adapter sources attribution
    # from `fills.ticker` (not `orders.ticker`) so it has per-contract
    # granularity for options; equity passes through unchanged.
    async with async_session_factory() as session:
        await session.execute(
            text("""
                INSERT INTO fills (id, created_at_utc, ticker, broker_order_id, client_order_id, filled_qty, side, raw_json)
                VALUES ('f1', CURRENT_TIMESTAMP, 'AAPL', 'b1', 'orion_test1', 10, 'buy', '{}'),
                       ('f2', CURRENT_TIMESTAMP, 'MSFT', 'b2', 'cerberus_test2', 10, 'buy', '{}')
            """)
        )
        await session.commit()

    # Mock Gateway returning all 3 positions
    fake_client = AsyncMock()
    fake_client.get_positions = AsyncMock(
        return_value=[
            {"symbol": "AAPL", "current_price": 180, "avg_entry_price": 175, "qty": 10, "unrealized_plpc": 0.03},
            {"symbol": "MSFT", "current_price": 350, "avg_entry_price": 340, "qty": 5, "unrealized_plpc": 0.03},
            {"symbol": "ABC", "current_price": 50, "avg_entry_price": 45, "qty": 2, "unrealized_plpc": 0.11},
        ]
    )

    adapter = GatewayPositionAdapter(fake_client)
    await adapter.refresh()

    symbols = {p.symbol for p in adapter.get_all_positions()}
    assert symbols == {"AAPL"}, (
        f"adapter must return only orion-attributed positions, got {symbols} — "
        f"MSFT (cerberus) and ABC (no order) must be excluded"
    )


@pytest.mark.asyncio
async def test_adapter_returns_empty_when_no_orion_orders() -> None:
    """If Orion has no orders at all (fresh database, account-shutdown
    state, etc.), the adapter must return an empty list — not all
    broker positions."""
    from orion.execution.position_monitor import GatewayPositionAdapter
    from orion.storage.db import init_db

    await init_db()  # creates empty tables

    fake_client = AsyncMock()
    fake_client.get_positions = AsyncMock(
        return_value=[
            {"symbol": "GOOG", "current_price": 150, "avg_entry_price": 145, "qty": 3, "unrealized_plpc": 0.03},
        ]
    )

    adapter = GatewayPositionAdapter(fake_client)
    await adapter.refresh()

    assert adapter.get_all_positions() == [], (
        "with no orion orders in DB, adapter must return empty list — "
        "default-deny prevents silently inheriting another system's positions"
    )


@pytest.mark.asyncio
async def test_adapter_keeps_orion_options_positions_per_occ_contract() -> None:
    """Positive case for per-OCC attribution: Orion filled
    ``AAPL260529P00315000``; broker reports that exact contract; the
    adapter retains it.

    Originally written 2026-05-26 as a regression for the underlying-
    only matching bug introduced in commit dca484d. Rewritten same day
    (codex review CRITICAL on commit 39174f8) to source attribution
    from ``fills.ticker`` — which stores the full OCC contract for
    options — instead of ``orders.ticker`` (underlying). The negative
    counterpart is
    ``test_adapter_excludes_other_systems_options_on_same_underlying``.

    Live impact of the original bug: 0 exit_decisions all day on
    2026-05-26 despite 39 fresh Orion options positions on the broker
    (many at -30%+ unrealized loss that should have hit stop-loss
    exits). The position monitor's filter was logging
    ``kept_count=0`` for every ``/positions`` sync.
    """
    from sqlalchemy import text

    from orion.execution.position_monitor import GatewayPositionAdapter
    from orion.storage.db import async_session_factory, init_db

    await init_db()

    # Orion filled the AAPL put contract. `fills.ticker` stores the
    # full OCC contract symbol for options.
    async with async_session_factory() as session:
        await session.execute(
            text("""
                INSERT INTO fills (id, created_at_utc, ticker, broker_order_id, client_order_id, filled_qty, side, raw_json)
                VALUES ('f1', CURRENT_TIMESTAMP, 'AAPL260529P00315000', 'b1', 'orion_aapl_put', 10, 'buy', '{}')
            """)
        )
        await session.commit()

    # Broker returns the same OCC contract plus an MSFT contract Orion
    # has never filled and an unrelated TSLA equity position.
    fake_client = AsyncMock()
    fake_client.get_positions = AsyncMock(
        return_value=[
            {
                "symbol": "AAPL260529P00315000",
                "current_price": 6.75,
                "avg_entry_price": 5.50,
                "qty": 10,
                "unrealized_plpc": 0.227,
            },
            {
                "symbol": "MSFT260529C00450000",
                "current_price": 12.0,
                "avg_entry_price": 10.0,
                "qty": 5,
                "unrealized_plpc": 0.2,
            },
            {
                "symbol": "TSLA",
                "current_price": 250,
                "avg_entry_price": 240,
                "qty": 1,
                "unrealized_plpc": 0.04,
            },
        ]
    )

    adapter = GatewayPositionAdapter(fake_client)
    await adapter.refresh()

    symbols = {p.symbol for p in adapter.get_all_positions()}
    assert symbols == {"AAPL260529P00315000"}, (
        f"adapter must keep the exact OCC contract Orion has filled; "
        f"got {symbols}. MSFT and TSLA have no orion fill, so they "
        f"belong to sibling systems and must be excluded."
    )


@pytest.mark.asyncio
async def test_adapter_excludes_other_systems_options_on_same_underlying() -> None:
    """Codex review 2026-05-26: per-position attribution, not per-underlying.

    Commit 39174f8 used underlying-only matching: any broker position
    whose underlying appeared anywhere in Orion's order history was
    treated as Orion-owned. Counterexample: Orion bought AAPL puts last
    week (so orders.ticker='AAPL' exists), Kairos opens an AAPL CALL
    today (different OCC contract, same underlying). The 39174f8 filter
    incorrectly admits Kairos's call → it counts in Orion's risk AND
    PositionMonitor.execute_exits routes it to ExecutionEngine.close_position,
    closing a position Orion doesn't own.

    Per-position attribution uses the FILLS table (fills.ticker stores
    the full OCC contract for options) — a contract Orion has actually
    filled is the only one Orion owns. Same-underlying sibling positions
    have different OCC contracts and are correctly excluded.
    """
    from sqlalchemy import text

    from orion.execution.position_monitor import GatewayPositionAdapter
    from orion.storage.db import async_session_factory, init_db

    await init_db()

    # Orion previously filled an AAPL put contract.
    async with async_session_factory() as session:
        await session.execute(
            text("""
                INSERT INTO fills (id, created_at_utc, ticker, broker_order_id, client_order_id, filled_qty, side, raw_json)
                VALUES ('f1', CURRENT_TIMESTAMP, 'AAPL260529P00315000', 'b1', 'orion_aapl_put', 10, 'buy', '{}')
            """)
        )
        await session.commit()

    # Broker now reports two AAPL options:
    #   1. AAPL260529P00315000 — the put Orion owns
    #   2. AAPL260529C00400000 — a CALL on the same underlying, opened
    #      by a sibling system (Kairos). Orion has NEVER filled this OCC.
    fake_client = AsyncMock()
    fake_client.get_positions = AsyncMock(
        return_value=[
            {
                "symbol": "AAPL260529P00315000",
                "current_price": 6.75,
                "avg_entry_price": 5.50,
                "qty": 10,
                "unrealized_plpc": 0.227,
            },
            {
                "symbol": "AAPL260529C00400000",
                "current_price": 3.20,
                "avg_entry_price": 2.80,
                "qty": 5,
                "unrealized_plpc": 0.143,
            },
        ]
    )

    adapter = GatewayPositionAdapter(fake_client)
    await adapter.refresh()

    symbols = {p.symbol for p in adapter.get_all_positions()}
    assert symbols == {"AAPL260529P00315000"}, (
        f"per-OCC attribution must EXCLUDE sibling-system AAPL options "
        f"Orion has never filled; got {symbols}. Underlying-only matching "
        f"(commit 39174f8) admitted both AAPL positions — that would close "
        f"Kairos's call when Orion's exit-pipeline evaluates the put's "
        f"stop-loss."
    )


@pytest.mark.asyncio
async def test_adapter_db_failure_returns_empty_not_unfiltered() -> None:
    """If the orion-ticker DB query raises, the adapter must fail
    SAFE (return empty) rather than fall back to the unfiltered
    broker positions. Anti-foot-gun for the shared-account scenario."""
    from orion.execution.position_monitor import GatewayPositionAdapter

    fake_client = AsyncMock()
    fake_client.get_positions = AsyncMock(
        return_value=[
            {"symbol": "TSLA", "current_price": 250, "avg_entry_price": 240, "qty": 5, "unrealized_plpc": 0.04},
        ]
    )

    adapter = GatewayPositionAdapter(fake_client)

    with patch(
        "orion.execution.position_monitor._fetch_orion_attributed_tickers",
        AsyncMock(side_effect=RuntimeError("simulated DB failure")),
    ):
        await adapter.refresh()

    assert adapter.get_all_positions() == [], (
        "DB failure must fail-safe to empty — never inherit unfiltered broker positions"
    )
