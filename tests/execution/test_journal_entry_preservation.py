"""Regression guard: closing a position must not overwrite entry fill data.

B3 RCA 2026-06-11: persist_realized_pnl_to_journal previously wrote the exit
fill timestamp into ``filled_at_utc`` — the entry fill column — destroying the
entry price/qty/time needed for cost-basis reconstruction and round-trip audit.
The fix routes exit data to exit_filled_* columns and leaves entry columns
untouched.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from orion.execution.persistence import persist_realized_pnl_to_journal
from orion.storage.db import async_session_factory
from orion.storage.models_trade_journal import TradeJournalEntry

pytestmark = pytest.mark.unit

ENTRY_TIME = datetime(2026, 6, 11, 10, 0, 0, tzinfo=UTC)
EXIT_TIME = ENTRY_TIME + timedelta(hours=2)


async def _seed_entry(decision_id: str, ticker: str) -> None:
    async with async_session_factory() as session:
        session.add(
            TradeJournalEntry(
                decision_id=decision_id,
                signal_id=f"sig_{decision_id}",
                ticker=ticker,
                direction="LONG",
                client_order_id=f"orion_entry_{decision_id}",
                broker_order_id=f"broker_entry_{decision_id}",
                filled_qty=3.0,
                filled_avg_price=5.50,
                filled_at_utc=ENTRY_TIME,
                realized_pnl=None,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_entry_fill_data_unchanged_after_close() -> None:
    """After a close, the entry fill fields must still hold entry values."""
    await _seed_entry("dec_preservation_1", "NVDA")

    await persist_realized_pnl_to_journal(
        ticker="NVDA",
        realized_pnl=9.0,
        exit_broker_order_id="broker_exit_1",
        filled_at=EXIT_TIME,
        exit_qty=3.0,
        exit_price=8.50,
    )

    async with async_session_factory() as session:
        row = (
            (await session.execute(select(TradeJournalEntry).where(TradeJournalEntry.ticker == "NVDA")))
            .scalars()
            .first()
        )

    assert row is not None

    # Entry fill data must be unchanged.
    assert row.filled_qty == pytest.approx(3.0)
    assert row.filled_avg_price == pytest.approx(5.50)
    # SQLite drops tz; compare year/month/day/hour to avoid tz-equality issues.
    assert row.filled_at_utc is not None
    assert row.filled_at_utc.year == ENTRY_TIME.year
    assert row.filled_at_utc.hour == ENTRY_TIME.hour

    # PnL written.
    assert row.realized_pnl == pytest.approx(9.0)

    # Exit-leg data written to dedicated columns.
    assert row.exit_broker_order_id == "broker_exit_1"
    assert row.exit_filled_qty == pytest.approx(3.0)
    assert row.exit_filled_avg_price == pytest.approx(8.50)
    assert row.exit_filled_at_utc is not None
    assert row.exit_filled_at_utc.hour == EXIT_TIME.hour


@pytest.mark.asyncio
async def test_exit_broker_order_id_written_to_dedicated_column() -> None:
    """exit_broker_order_id must land on its own column, not only in notes."""
    await _seed_entry("dec_preservation_2", "AMD")

    await persist_realized_pnl_to_journal(
        ticker="AMD",
        realized_pnl=4.0,
        exit_broker_order_id="broker_exit_amd",
    )

    async with async_session_factory() as session:
        row = (
            (await session.execute(select(TradeJournalEntry).where(TradeJournalEntry.ticker == "AMD")))
            .scalars()
            .first()
        )

    assert row is not None
    assert row.exit_broker_order_id == "broker_exit_amd"
    # notes backward-compat still present
    assert row.notes == "closed_by=broker_exit_amd"
    # Entry data untouched (no exit_filled_at given, filled_at_utc stays as seeded)
    assert row.filled_qty == pytest.approx(3.0)
    assert row.filled_avg_price == pytest.approx(5.50)


@pytest.mark.asyncio
async def test_no_open_entry_logs_warning_does_not_raise() -> None:
    """When no open journal row exists, the function must return without raising."""
    # Should complete without error even though MSFT has no journal entry.
    await persist_realized_pnl_to_journal(
        ticker="MSFT",
        realized_pnl=1.0,
        exit_broker_order_id="broker_exit_msft",
    )
