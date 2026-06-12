"""B2 RCA: realized PnL from a closing fill must be written back to the
originating entry's trade-journal row.

Before this fix, ``persist_fill_record`` matched the journal only by the
fill's ``broker_order_id`` — which on an EXIT fill never matches the ENTRY
journal row — so ``trade_journal_entries.realized_pnl`` stayed NULL for every
trade and EOD/weekly PnL reporting was blind.
"""

import os

os.environ["DB_URL"] = "sqlite+aiosqlite:///:memory:"

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from orion.execution.fill_processor import FillProcessor
from orion.execution.risk.manager import RiskManager
from orion.storage.db import async_session_factory, init_db
from orion.storage.models_trade_journal import TradeJournalEntry


async def _noop_remove(_client_oid: str) -> None:
    return None


@pytest.mark.asyncio
async def test_closing_fill_writes_realized_pnl_to_entry_journal_row():
    await init_db()

    rm = RiskManager()
    await rm.initialize()
    # Open long position: 2 contracts @ 5.00 (the entry).
    rm.positions["MU"] = {"qty": 2.0, "avg_entry": 5.0}

    # The entry journal row: entered + filled, PnL still open (NULL).
    async with async_session_factory() as session:
        session.add(
            TradeJournalEntry(
                decision_id="dec_test_mu",
                signal_id="sig_test_mu",
                candidate_id="cand_mu",
                ticker="MU",
                direction="LONG",
                client_order_id="orion_entry_mu",
                broker_order_id="broker_entry_mu",
                filled_qty=2.0,
                filled_avg_price=5.0,
                filled_at_utc=datetime.now(UTC),
                realized_pnl=None,
            )
        )
        await session.commit()

    # A CLOSING fill arrives: sell 2 @ 8.00 → realized = (8 - 5) * 2 = 6.0
    fp = FillProcessor()
    closing_fill = {
        "id": "broker_exit_mu",
        "client_order_id": "orion_exit_mu",
        "symbol": "MU",
        "side": "sell",
        "qty": 2,
        "filled_qty": 2,
        "filled_avg_price": 8,
        "filled_at": datetime.now(UTC).isoformat(),
    }
    await fp.process_single_fill(closing_fill, rm, _noop_remove)

    async with async_session_factory() as session:
        row = (
            (await session.execute(select(TradeJournalEntry).where(TradeJournalEntry.ticker == "MU"))).scalars().first()
        )

    assert row is not None
    assert row.realized_pnl == pytest.approx(6.0)


@pytest.mark.asyncio
async def test_closing_fill_stamps_exit_timestamp_for_multi_day_bucketing():
    """Round-7: the LIVE fill path must write the close fill's timestamp to
    ``exit_filled_at_utc``. ``filled_at_utc`` is the preserved ENTRY time (B3),
    so leaving the exit stamp NULL makes the PnL reconciliation fall back to the
    entry day — putting a multi-day close on the wrong day. No manual seeding of
    exit_filled_at_utc here: the row must come out of FillProcessor itself.
    """
    from datetime import date

    from orion.jobs import reconcile_pnl

    await init_db()

    rm = RiskManager()
    await rm.initialize()
    rm.positions["NVDA"] = {"qty": 2.0, "avg_entry": 5.0}

    entry_ts = datetime(2026, 6, 9, 15, 0, tzinfo=UTC)
    close_ts = datetime(2026, 6, 11, 15, 30, tzinfo=UTC)

    async with async_session_factory() as session:
        session.add(
            TradeJournalEntry(
                decision_id="dec_test_nvda",
                signal_id="sig_test_nvda",
                candidate_id="cand_nvda",
                ticker="NVDA",
                direction="LONG",
                client_order_id="orion_entry_nvda",
                broker_order_id="broker_entry_nvda",
                filled_qty=2.0,
                filled_avg_price=5.0,
                filled_at_utc=entry_ts,
                realized_pnl=None,
            )
        )
        await session.commit()

    fp = FillProcessor()
    closing_fill = {
        "id": "broker_exit_nvda",
        "client_order_id": "orion_exit_nvda",
        "symbol": "NVDA",
        "side": "sell",
        "qty": 2,
        "filled_qty": 2,
        "filled_avg_price": 8,
        "filled_at": close_ts.isoformat(),
    }
    await fp.process_single_fill(closing_fill, rm, _noop_remove)

    async with async_session_factory() as session:
        row = (
            (await session.execute(select(TradeJournalEntry).where(TradeJournalEntry.ticker == "NVDA")))
            .scalars()
            .first()
        )

    assert row is not None
    assert row.realized_pnl == pytest.approx(6.0)
    # Exit stamp written by the live path; entry provenance preserved.
    assert row.exit_filled_at_utc is not None
    assert row.exit_filled_at_utc.replace(tzinfo=UTC) == close_ts
    assert row.filled_at_utc.replace(tzinfo=UTC) == entry_ts

    # And the reconciliation buckets this row on the CLOSE day, not entry day.
    close_day_rows = await reconcile_pnl._load_journal_closed_trades(date(2026, 6, 11))
    assert [r["realized_pnl"] for r in close_day_rows] == [pytest.approx(6.0)]
    entry_day_rows = await reconcile_pnl._load_journal_closed_trades(date(2026, 6, 9))
    assert entry_day_rows == []


@pytest.mark.asyncio
async def test_entry_fill_does_not_write_realized_pnl():
    """An opening fill must NOT touch realized_pnl (nothing realized yet)."""
    await init_db()

    rm = RiskManager()
    await rm.initialize()

    async with async_session_factory() as session:
        session.add(
            TradeJournalEntry(
                decision_id="dec_test_aapl",
                signal_id="sig_test_aapl",
                candidate_id="cand_aapl",
                ticker="AAPL",
                direction="LONG",
                client_order_id="orion_entry_aapl",
                broker_order_id="broker_entry_aapl",
                realized_pnl=None,
            )
        )
        await session.commit()

    fp = FillProcessor()
    entry_fill = {
        "id": "broker_entry_aapl_2",
        "client_order_id": "orion_entry_aapl_2",
        "symbol": "AAPL",
        "side": "buy",
        "qty": 3,
        "filled_qty": 3,
        "filled_avg_price": 10,
        "filled_at": datetime.now(UTC).isoformat(),
    }
    await fp.process_single_fill(entry_fill, rm, _noop_remove)

    async with async_session_factory() as session:
        row = (
            (await session.execute(select(TradeJournalEntry).where(TradeJournalEntry.ticker == "AAPL")))
            .scalars()
            .first()
        )

    assert row is not None
    assert row.realized_pnl is None
