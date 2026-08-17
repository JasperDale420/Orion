"""Exit-fill → trade-journal allocation.

A journal row is one entry LOT (``filled_qty`` @ ``filled_avg_price``). Closing
fills arrive with the OCC contract symbol (``NVDA260708C00190000``) while the
journal row carries the underlying (``NVDA``); the allocator resolves lots via
the candidate's ``option_symbol`` and allocates the closing order's cumulative
fill across open lots FIFO, per lot, with an idempotent per-order ledger in
``raw_json["exit_allocations"]``. ``realized_pnl`` is written only when a lot is
fully closed, so partially closed lots keep counting toward the entry caps.
"""

import os

os.environ["DB_URL"] = "sqlite+aiosqlite:///:memory:"

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from orion.execution.persistence import (
    allocate_exit_to_journal,
    count_open_journal_positions,
    realize_expired_journal_rows,
    reconcile_journal_exits_from_fills,
)
from orion.storage.db import async_session_factory, init_db
from orion.storage.models_execution import FillRecord
from orion.storage.models_gold import CandidateTrade
from orion.storage.models_trade_journal import TradeJournalEntry

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 7, 14, 0, tzinfo=UTC)
NVDA_OCC = "NVDA260708C00190000"
SPY_OCC = "SPY260707P00745000"


def _candidate(cid: str, ticker: str, occ: str, expiration: datetime) -> CandidateTrade:
    return CandidateTrade(
        candidate_id=cid,
        ticker=ticker,
        timestamp_utc=NOW - timedelta(days=1),
        rule_id="rule_swing_v2",
        direction="LONG",
        option_symbol=occ,
        expiration_date=expiration,
        evidence={},
    )


def _lot(
    decision_id: str,
    cid: str,
    ticker: str,
    qty: float,
    price: float,
    *,
    created_offset_s: int = 0,
) -> TradeJournalEntry:
    return TradeJournalEntry(
        decision_id=decision_id,
        created_at_utc=NOW - timedelta(hours=2) + timedelta(seconds=created_offset_s),
        signal_id=f"sig_{decision_id}",
        candidate_id=cid,
        ticker=ticker,
        direction="LONG",
        client_order_id=f"orion_{decision_id}",
        broker_order_id=f"broker_{decision_id}",
        filled_qty=qty,
        filled_avg_price=price,
        filled_at_utc=NOW - timedelta(hours=1),
        raw_json={},
    )


async def _seed(*rows: object) -> None:
    async with async_session_factory() as session:
        for r in rows:
            session.add(r)
        await session.commit()


async def _row(decision_id: str) -> TradeJournalEntry:
    async with async_session_factory() as session:
        row = (
            await session.execute(select(TradeJournalEntry).where(TradeJournalEntry.decision_id == decision_id))
        ).scalar_one()
        session.expunge(row)
        return row


@pytest.mark.asyncio
async def test_closing_option_fill_links_journal_by_option_symbol() -> None:
    """The exit fill carries the OCC symbol; the lot carries the underlying."""
    await init_db()
    exp = datetime(2026, 7, 8, tzinfo=UTC)
    await _seed(_candidate("c1", "NVDA", NVDA_OCC, exp), _lot("d1", "c1", "NVDA", 1, 2.85))

    result = await allocate_exit_to_journal(
        contract=NVDA_OCC,
        order_id="exit_ord_1",
        order_cum_qty=1,
        order_cum_avg_price=4.45,
        filled_at=NOW,
        source="live",
    )
    assert result.allocated_qty == pytest.approx(1)
    assert result.unmatched_qty == pytest.approx(0)

    row = await _row("d1")
    assert row.realized_pnl == pytest.approx((4.45 - 2.85) * 1 * 100)
    assert row.exit_filled_qty == pytest.approx(1)
    assert row.exit_filled_avg_price == pytest.approx(4.45)
    assert row.exit_broker_order_id == "exit_ord_1"
    assert row.exit_filled_at_utc is not None
    assert row.notes == "closed_by=exit_ord_1"
    # Entry leg untouched.
    assert row.filled_qty == pytest.approx(1)
    assert row.filled_avg_price == pytest.approx(2.85)
    # Ledger persisted (JSON column must be reassigned, not mutated).
    assert row.raw_json["exit_allocations"] == [
        {"order_id": "exit_ord_1", "qty": 1.0, "price": 4.45, "at": NOW.isoformat(), "source": "live"}
    ]


@pytest.mark.asyncio
async def test_multi_lot_close_allocates_fifo_with_per_lot_pnl() -> None:
    """Three 1-lot SPY entries at different prices closed by ONE 3-lot sell."""
    await init_db()
    exp = datetime(2026, 7, 7, tzinfo=UTC)
    await _seed(
        _candidate("c1", "SPY", SPY_OCC, exp),
        _candidate("c2", "SPY", SPY_OCC, exp),
        _candidate("c3", "SPY", SPY_OCC, exp),
        _lot("d1", "c1", "SPY", 1, 2.89, created_offset_s=0),
        _lot("d2", "c2", "SPY", 1, 2.90, created_offset_s=5),
        _lot("d3", "c3", "SPY", 1, 2.85, created_offset_s=10),
    )
    counts = await count_open_journal_positions()
    assert counts is not None
    by_bucket, _ = counts
    assert sum(by_bucket.values()) == 3

    result = await allocate_exit_to_journal(
        contract=SPY_OCC, order_id="exit_spy", order_cum_qty=3, order_cum_avg_price=2.00, filled_at=NOW, source="live"
    )
    assert result.allocated_qty == pytest.approx(3)
    assert result.closed_rows == 3

    for did, entry in (("d1", 2.89), ("d2", 2.90), ("d3", 2.85)):
        row = await _row(did)
        assert row.realized_pnl == pytest.approx((2.00 - entry) * 100)
        assert row.exit_filled_qty == pytest.approx(1)

    counts = await count_open_journal_positions()
    assert counts is not None
    by_bucket, _ = counts
    assert sum(by_bucket.values()) == 0


@pytest.mark.asyncio
async def test_partial_close_keeps_lot_open_and_counted() -> None:
    """1 of 3 contracts sold: lot stays open (caps still count it), no realized_pnl yet."""
    await init_db()
    exp = datetime(2026, 7, 10, tzinfo=UTC)
    await _seed(_candidate("c1", "ORCL", "ORCL260710P00135000", exp), _lot("d1", "c1", "ORCL", 3, 1.45))

    await allocate_exit_to_journal(
        contract="ORCL260710P00135000",
        order_id="exit_a",
        order_cum_qty=1,
        order_cum_avg_price=1.50,
        filled_at=NOW,
        source="live",
    )
    row = await _row("d1")
    assert row.realized_pnl is None
    assert row.exit_filled_qty == pytest.approx(1)
    assert row.exit_filled_avg_price == pytest.approx(1.50)
    counts = await count_open_journal_positions()
    assert counts is not None
    by_bucket, _ = counts
    assert sum(by_bucket.values()) == 1

    # Second (separate) order closes the remaining 2 at a different price.
    await allocate_exit_to_journal(
        contract="ORCL260710P00135000",
        order_id="exit_b",
        order_cum_qty=2,
        order_cum_avg_price=1.20,
        filled_at=NOW + timedelta(minutes=5),
        source="live",
    )
    row = await _row("d1")
    assert row.exit_filled_qty == pytest.approx(3)
    assert row.exit_filled_avg_price == pytest.approx((1 * 1.50 + 2 * 1.20) / 3)
    assert row.realized_pnl == pytest.approx(((1 * 1.50 + 2 * 1.20) / 3 - 1.45) * 3 * 100)
    assert row.exit_broker_order_id == "exit_b"
    counts = await count_open_journal_positions()
    assert counts is not None
    by_bucket, _ = counts
    assert sum(by_bucket.values()) == 0


@pytest.mark.asyncio
async def test_cumulative_partial_fill_at_unequal_prices_uses_leg_price() -> None:
    """Broker reports cumulative qty/avg: 1@1.00 then 2@2.00 means the 2nd leg was 1@3.00."""
    await init_db()
    exp = datetime(2026, 7, 10, tzinfo=UTC)
    await _seed(_candidate("c1", "IREN", "IREN260710P00037000", exp), _lot("d1", "c1", "IREN", 2, 1.00))

    await allocate_exit_to_journal(
        contract="IREN260710P00037000",
        order_id="exit_x",
        order_cum_qty=1,
        order_cum_avg_price=1.00,
        filled_at=NOW,
        source="live",
    )
    await allocate_exit_to_journal(
        contract="IREN260710P00037000",
        order_id="exit_x",
        order_cum_qty=2,
        order_cum_avg_price=2.00,
        filled_at=NOW + timedelta(seconds=30),
        source="live",
    )
    row = await _row("d1")
    legs = row.raw_json["exit_allocations"]
    assert [(leg["qty"], leg["price"]) for leg in legs] == [(1.0, 1.0), (1.0, 3.0)]
    assert row.exit_filled_qty == pytest.approx(2)
    assert row.exit_filled_avg_price == pytest.approx(2.00)
    assert row.realized_pnl == pytest.approx((2.00 - 1.00) * 2 * 100)


@pytest.mark.asyncio
async def test_duplicate_delivery_of_same_order_allocates_once() -> None:
    """Live path then EOD reconcile (or two live processes) deliver the same cumulative fill."""
    await init_db()
    exp = datetime(2026, 7, 8, tzinfo=UTC)
    await _seed(_candidate("c1", "NVDA", NVDA_OCC, exp), _lot("d1", "c1", "NVDA", 1, 2.85))

    first = await allocate_exit_to_journal(
        contract=NVDA_OCC, order_id="exit_1", order_cum_qty=1, order_cum_avg_price=4.45, filled_at=NOW, source="live"
    )
    second = await allocate_exit_to_journal(
        contract=NVDA_OCC,
        order_id="exit_1",
        order_cum_qty=1,
        order_cum_avg_price=4.45,
        filled_at=NOW,
        source="eod_reconcile",
    )
    assert first.allocated_qty == pytest.approx(1)
    assert second.allocated_qty == pytest.approx(0)
    row = await _row("d1")
    assert len(row.raw_json["exit_allocations"]) == 1
    assert row.exit_filled_qty == pytest.approx(1)


@pytest.mark.asyncio
async def test_sell_with_no_open_lot_is_unmatched_and_writes_nothing() -> None:
    await init_db()
    result = await allocate_exit_to_journal(
        contract="BABA260717C00105000",
        order_id="exit_z",
        order_cum_qty=4,
        order_cum_avg_price=1.0,
        filled_at=NOW,
        source="live",
    )
    assert result.allocated_qty == pytest.approx(0)
    assert result.unmatched_qty == pytest.approx(4)


@pytest.mark.asyncio
async def test_underlying_ticker_fallback_still_matches_equity_lot() -> None:
    """A lot with no candidate option symbol is matched on the underlying, x1 multiplier."""
    await init_db()
    async with async_session_factory() as session:
        session.add(
            TradeJournalEntry(
                decision_id="d_eq",
                signal_id="sig",
                ticker="AAPL",
                direction="LONG",
                client_order_id="orion_eq",
                broker_order_id="broker_eq",
                filled_qty=10,
                filled_avg_price=100.0,
                filled_at_utc=NOW - timedelta(hours=1),
                raw_json={},
            )
        )
        await session.commit()
    await allocate_exit_to_journal(
        contract="AAPL", order_id="exit_eq", order_cum_qty=10, order_cum_avg_price=101.0, filled_at=NOW, source="live"
    )
    row = await _row("d_eq")
    assert row.realized_pnl == pytest.approx((101.0 - 100.0) * 10)


@pytest.mark.asyncio
async def test_reconcile_from_fills_links_missed_close_and_is_idempotent() -> None:
    """A sell fill recorded in `fills` but never attributed (restart, out-of-order poll) is picked up at EOD."""
    await init_db()
    exp = datetime(2026, 7, 8, tzinfo=UTC)
    await _seed(
        _candidate("c1", "NVDA", NVDA_OCC, exp),
        _lot("d1", "c1", "NVDA", 1, 2.85),
        FillRecord(
            id="f1",
            ticker=NVDA_OCC,
            broker_order_id="bo_sell_1",
            client_order_id="orion_exit_1",
            filled_qty=1,
            filled_avg_price=4.45,
            side="sell",
            filled_at_utc=NOW,
            raw_json={},
        ),
        # A buy fill and a non-Orion sell must be ignored.
        FillRecord(
            id="f2",
            ticker=NVDA_OCC,
            broker_order_id="bo_buy_1",
            client_order_id="orion_entry_1",
            filled_qty=1,
            filled_avg_price=2.85,
            side="buy",
            filled_at_utc=NOW - timedelta(hours=1),
            raw_json={},
        ),
        FillRecord(
            id="f3",
            ticker=NVDA_OCC,
            broker_order_id="bo_other",
            client_order_id="kairos_x",
            filled_qty=1,
            filled_avg_price=9.99,
            side="sell",
            filled_at_utc=NOW,
            raw_json={},
        ),
    )
    n = await reconcile_journal_exits_from_fills()
    assert n == 1
    row = await _row("d1")
    assert row.realized_pnl == pytest.approx((4.45 - 2.85) * 100)
    assert row.exit_broker_order_id == "bo_sell_1"

    assert await reconcile_journal_exits_from_fills() == 0
    row = await _row("d1")
    assert len(row.raw_json["exit_allocations"]) == 1


@pytest.mark.asyncio
async def test_reconcile_ignores_sell_fills_before_entry() -> None:
    """A sell that pre-dates the entry belongs to an earlier lot (June carry-over), not this one."""
    await init_db()
    exp = datetime(2026, 7, 8, tzinfo=UTC)
    await _seed(
        _candidate("c1", "NVDA", NVDA_OCC, exp),
        _lot("d1", "c1", "NVDA", 1, 2.85),
        FillRecord(
            id="f_old",
            ticker=NVDA_OCC,
            broker_order_id="bo_old",
            client_order_id="orion_old",
            filled_qty=1,
            filled_avg_price=3.0,
            side="sell",
            filled_at_utc=NOW - timedelta(days=3),
            raw_json={},
        ),
    )
    assert await reconcile_journal_exits_from_fills() == 0
    row = await _row("d1")
    assert row.realized_pnl is None


@pytest.mark.asyncio
async def test_expiry_sweep_books_only_unsold_remainder_after_partial_sale() -> None:
    """2 of 3 sold at 1.50; 1 expires worthless → P&L = 2*(1.50-1.10)*100 - 1*1.10*100."""
    await init_db()
    expired = NOW - timedelta(days=3)
    await _seed(_candidate("c1", "IONQ", "IONQ260704C00040000", expired), _lot("d1", "c1", "IONQ", 3, 1.10))
    await allocate_exit_to_journal(
        contract="IONQ260704C00040000",
        order_id="exit_p",
        order_cum_qty=2,
        order_cum_avg_price=1.50,
        filled_at=NOW - timedelta(days=4),
        source="live",
    )
    # Sweep runs "now" relative to wall clock; expiration is well in the past.
    count = await realize_expired_journal_rows()
    assert count == 1
    row = await _row("d1")
    assert row.realized_pnl == pytest.approx(2 * (1.50 - 1.10) * 100 - 1 * 1.10 * 100)
    assert row.notes == "expired_worthless"
    assert row.exit_filled_qty == pytest.approx(3)
    legs = row.raw_json["exit_allocations"]
    assert legs[-1]["source"] == "expired" and legs[-1]["qty"] == pytest.approx(1)


@pytest.mark.asyncio
async def test_expiry_sweep_never_touches_a_fully_sold_lot() -> None:
    await init_db()
    expired = NOW - timedelta(days=3)
    await _seed(_candidate("c1", "IONQ", "IONQ260704C00040000", expired), _lot("d1", "c1", "IONQ", 1, 1.10))
    await allocate_exit_to_journal(
        contract="IONQ260704C00040000",
        order_id="exit_full",
        order_cum_qty=1,
        order_cum_avg_price=1.50,
        filled_at=NOW - timedelta(days=4),
        source="live",
    )
    assert await realize_expired_journal_rows() == 0
    row = await _row("d1")
    assert row.notes == "closed_by=exit_full"
    assert row.realized_pnl == pytest.approx((1.50 - 1.10) * 100)


@pytest.mark.asyncio
async def test_inconsistent_cumulative_delta_is_refused_not_substituted() -> None:
    """1@1.00 then cumulative 2@0.50 implies a 0.00 second leg — broker figures are inconsistent
    with an option print; refuse the delta loudly rather than book a made-up price."""
    await init_db()
    exp = datetime(2026, 7, 10, tzinfo=UTC)
    await _seed(_candidate("c1", "IREN", "IREN260710P00037000", exp), _lot("d1", "c1", "IREN", 2, 1.00))
    await allocate_exit_to_journal(
        contract="IREN260710P00037000",
        order_id="exit_x",
        order_cum_qty=1,
        order_cum_avg_price=1.00,
        filled_at=NOW,
        source="live",
    )
    result = await allocate_exit_to_journal(
        contract="IREN260710P00037000",
        order_id="exit_x",
        order_cum_qty=2,
        order_cum_avg_price=0.50,
        filled_at=NOW + timedelta(seconds=30),
        source="live",
    )
    assert result.allocated_qty == pytest.approx(0)
    assert result.unmatched_qty == pytest.approx(1)
    row = await _row("d1")
    assert row.exit_filled_qty == pytest.approx(1)
    assert row.realized_pnl is None
    assert len(row.raw_json["exit_allocations"]) == 1


@pytest.mark.asyncio
async def test_allocation_scoped_to_decision_ids_never_touches_other_lots() -> None:
    """The repair path passes an explicit lot scope; lots outside it are invisible to allocation."""
    await init_db()
    exp = datetime(2026, 7, 7, tzinfo=UTC)
    await _seed(
        _candidate("c1", "SPY", SPY_OCC, exp),
        _candidate("c2", "SPY", SPY_OCC, exp),
        _lot("d1", "c1", "SPY", 1, 2.89, created_offset_s=0),
        _lot("d2", "c2", "SPY", 1, 2.90, created_offset_s=5),
    )
    from orion.execution.persistence import allocate_exit_in_session
    from orion.shared.db_utils import db_write

    async def go(session):  # type: ignore[no-untyped-def]
        return await allocate_exit_in_session(
            session,
            contract=SPY_OCC,
            order_id="exit_spy",
            order_cum_qty=2,
            order_cum_avg_price=2.00,
            filled_at=NOW,
            source="eod_reconcile",
            only_decision_ids={"d2"},
        )

    result = await db_write(go)
    assert result.allocated_qty == pytest.approx(1)
    assert result.unmatched_qty == pytest.approx(1)
    assert (await _row("d1")).realized_pnl is None
    assert (await _row("d2")).realized_pnl == pytest.approx((2.00 - 2.90) * 100)
