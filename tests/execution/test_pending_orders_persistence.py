"""Tests for pending-order DB persistence.

H-04 from predict 260430-1751-execution-reliability flagged that
`RiskManager.pending_orders` was in-memory-only — a restart between order
submission and fill lost the in-flight exposure tracking until the next
Gateway sync, under-counting exposure for the first ~30s post-restart.

The fix mirrors the in-memory dict to a `pending_orders` table:
  - update_post_trade upserts a row
  - remove_pending_order deletes the row (now async)
  - RiskManager.initialize loads fresh rows back into memory; rows older
    than PENDING_ORDER_LOAD_TTL_SECONDS are dropped as stale.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

# Force in-memory SQLite for unit tests
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///:memory:")

from sqlalchemy import select

from orion.config import RiskSettings
from orion.execution.risk.manager import RiskManager
from orion.storage.db import async_session_factory, init_db
from orion.storage.models_risk import PendingOrder


async def _wipe_pending_orders() -> None:
    """Reset the table between tests."""
    async with async_session_factory() as session:
        rows = list((await session.execute(select(PendingOrder))).scalars().all())
        for row in rows:
            await session.delete(row)
        await session.commit()


@pytest.mark.integration
@pytest.mark.asyncio
class TestUpdatePostTradePersists:
    async def test_buy_order_persists_with_positive_signed_cost(self) -> None:
        await init_db()
        await _wipe_pending_orders()

        rm = RiskManager(config=RiskSettings())
        await rm.update_post_trade("AAPL", qty=10, price=100.0, side="BUY", order_id="order_buy_1")

        async with async_session_factory() as session:
            row = (
                (await session.execute(select(PendingOrder).where(PendingOrder.order_id == "order_buy_1")))
                .scalars()
                .first()
            )

        assert row is not None
        assert row.ticker == "AAPL"
        assert row.signed_cost == 1000.0  # +cost on BUY

    async def test_sell_order_persists_with_negative_signed_cost(self) -> None:
        await init_db()
        await _wipe_pending_orders()

        rm = RiskManager(config=RiskSettings())
        await rm.update_post_trade("AAPL", qty=5, price=200.0, side="SELL", order_id="order_sell_1")

        async with async_session_factory() as session:
            row = (
                (await session.execute(select(PendingOrder).where(PendingOrder.order_id == "order_sell_1")))
                .scalars()
                .first()
            )

        assert row is not None
        assert row.signed_cost == -1000.0  # -cost on SELL

    async def test_re_submission_upserts_same_row(self) -> None:
        """Updating a pending order with the same id should overwrite, not duplicate."""
        await init_db()
        await _wipe_pending_orders()

        rm = RiskManager(config=RiskSettings())
        await rm.update_post_trade("AAPL", qty=10, price=100.0, side="BUY", order_id="upsert_test")
        await rm.update_post_trade("AAPL", qty=20, price=100.0, side="BUY", order_id="upsert_test")

        async with async_session_factory() as session:
            rows = list(
                (await session.execute(select(PendingOrder).where(PendingOrder.order_id == "upsert_test")))
                .scalars()
                .all()
            )

        assert len(rows) == 1
        assert rows[0].signed_cost == 2000.0


@pytest.mark.integration
@pytest.mark.asyncio
class TestRemovePendingOrderDeletes:
    async def test_remove_deletes_row(self) -> None:
        await init_db()
        await _wipe_pending_orders()

        rm = RiskManager(config=RiskSettings())
        await rm.update_post_trade("AAPL", qty=10, price=100.0, side="BUY", order_id="del_test")

        await rm.remove_pending_order("del_test")

        async with async_session_factory() as session:
            row = (
                (await session.execute(select(PendingOrder).where(PendingOrder.order_id == "del_test")))
                .scalars()
                .first()
            )

        assert row is None
        assert "del_test" not in rm.pending_orders

    async def test_remove_unknown_order_id_is_noop(self) -> None:
        """Removing an id that's not in memory or DB must not raise."""
        await init_db()
        await _wipe_pending_orders()

        rm = RiskManager(config=RiskSettings())
        # Should not raise
        await rm.remove_pending_order("never_existed")


@pytest.mark.integration
@pytest.mark.asyncio
class TestLoadPendingOrders:
    async def test_load_restores_fresh_rows_into_memory(self) -> None:
        await init_db()
        await _wipe_pending_orders()

        # Plant a fresh pending row directly
        async with async_session_factory() as session:
            session.add(
                PendingOrder(
                    order_id="restored_1",
                    ticker="MSFT",
                    signed_cost=15000.0,
                    created_at_utc=datetime.now(UTC),
                )
            )
            await session.commit()

        rm = RiskManager(config=RiskSettings())
        assert "restored_1" not in rm.pending_orders

        await rm._load_pending_orders()

        assert "restored_1" in rm.pending_orders
        assert rm.pending_orders["restored_1"] == ("MSFT", 15000.0)

    async def test_stale_rows_are_dropped_on_load(self) -> None:
        """Rows older than PENDING_ORDER_LOAD_TTL_SECONDS are discarded as
        almost certainly orphaned by a prior crash."""
        await init_db()
        await _wipe_pending_orders()

        stale_age_seconds = RiskManager.PENDING_ORDER_LOAD_TTL_SECONDS + 60
        async with async_session_factory() as session:
            session.add(
                PendingOrder(
                    order_id="stale_1",
                    ticker="TSLA",
                    signed_cost=5000.0,
                    created_at_utc=datetime.now(UTC) - timedelta(seconds=stale_age_seconds),
                )
            )
            session.add(
                PendingOrder(
                    order_id="fresh_1",
                    ticker="NVDA",
                    signed_cost=3000.0,
                    created_at_utc=datetime.now(UTC),
                )
            )
            await session.commit()

        rm = RiskManager(config=RiskSettings())
        await rm._load_pending_orders()

        assert "stale_1" not in rm.pending_orders
        assert "fresh_1" in rm.pending_orders

        # Stale row also deleted from DB so it doesn't accumulate
        async with async_session_factory() as session:
            stale_row = (
                (await session.execute(select(PendingOrder).where(PendingOrder.order_id == "stale_1")))
                .scalars()
                .first()
            )
        assert stale_row is None


@pytest.mark.integration
@pytest.mark.asyncio
class TestRuntimePruneStalePendingOrders:
    async def test_prune_drops_stale_keeps_fresh(self) -> None:
        """Runtime prune (not just on load) drops rows older than the TTL from
        BOTH memory and DB, so an expired day-order never cleared by a fill
        doesn't linger as phantom exposure until restart (RCA 2026-06-05:
        RBLX/ETSY/QURE)."""
        await init_db()
        await _wipe_pending_orders()

        stale_age = RiskManager.PENDING_ORDER_LOAD_TTL_SECONDS + 60
        async with async_session_factory() as session:
            session.add(
                PendingOrder(
                    order_id="stale_x",
                    ticker="RBLX",
                    signed_cost=3.1,
                    created_at_utc=datetime.now(UTC) - timedelta(seconds=stale_age),
                )
            )
            session.add(
                PendingOrder(
                    order_id="fresh_x",
                    ticker="NVDA",
                    signed_cost=3000.0,
                    created_at_utc=datetime.now(UTC),
                )
            )
            await session.commit()

        rm = RiskManager(config=RiskSettings())
        # Mirror the long-running-process state: both entries live in memory
        # because no fill ever cleared them.
        rm.pending_orders["stale_x"] = ("RBLX", 3.1)
        rm.pending_orders["fresh_x"] = ("NVDA", 3000.0)

        pruned = await rm.prune_stale_pending_orders()

        assert pruned == 1
        assert "stale_x" not in rm.pending_orders
        assert "fresh_x" in rm.pending_orders

        async with async_session_factory() as session:
            remaining = {r.order_id for r in (await session.execute(select(PendingOrder))).scalars().all()}
        assert remaining == {"fresh_x"}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_restart_round_trip_preserves_pending_exposure() -> None:
    """End-to-end: simulate submit → restart → check that pending exposure
    is still counted in `_calculate_projected_exposure`."""
    await init_db()
    await _wipe_pending_orders()

    # First instance: submit pending order
    rm1 = RiskManager(config=RiskSettings())
    await rm1.update_post_trade("COIN", qty=10, price=200.0, side="BUY", order_id="restart_oid")

    # Second instance: simulates a process restart
    rm2 = RiskManager(config=RiskSettings())
    assert "restart_oid" not in rm2.pending_orders  # starts empty

    await rm2._load_pending_orders()
    assert "restart_oid" in rm2.pending_orders

    # Pending exposure is now visible to the projected-exposure calculator
    projected, effective = rm2._calculate_projected_exposure(
        ticker="COIN", estimated_cost=1000.0, side="buy", price=200.0
    )
    # 0 (no current pos) + 2000 pending + 1000 new BUY = 3000 projected
    assert projected == pytest.approx(3000.0)
