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
    order in the DB. Tracker should see only that 1."""
    from sqlalchemy import text

    from orion.execution.position_monitor import GatewayPositionAdapter
    from orion.storage.db import async_session_factory, init_db

    await init_db()

    # Plant an Orion-attributed order for AAPL and a non-orion order
    # for MSFT. ABC has no order at all.
    async with async_session_factory() as session:
        await session.execute(
            text("""
                INSERT INTO orders (id, created_at_utc, ticker, side, qty, client_order_id, status, raw_json, system)
                VALUES ('o1', CURRENT_TIMESTAMP, 'AAPL', 'buy', 10, 'orion_test1', 'filled', '{}', 'orion'),
                       ('o2', CURRENT_TIMESTAMP, 'MSFT', 'buy', 10, 'cerberus_test2', 'filled', '{}', 'cerberus')
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
