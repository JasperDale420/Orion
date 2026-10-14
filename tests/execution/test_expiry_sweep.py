"""Expiry-worthless realization sweep.

An expired option produces no closing fill, so the fill-driven P&L path
(`allocate_exit_to_journal`) never runs and the journal row stays
open forever. `realize_expired_journal_rows` closes that gap: entries whose
candidate's expiration passed more than a day ago are realized at a total
loss of the entry premium and tagged `expired_worthless`.
"""

import os

os.environ["DB_URL"] = "sqlite+aiosqlite:///:memory:"

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from orion.execution.persistence import realize_expired_journal_rows
from orion.storage.db import async_session_factory, init_db
from orion.storage.models_gold import CandidateTrade
from orion.storage.models_trade_journal import TradeJournalEntry


def _candidate(candidate_id: str, ticker: str, expiration: datetime | None) -> CandidateTrade:
    return CandidateTrade(
        candidate_id=candidate_id,
        ticker=ticker,
        timestamp_utc=datetime.now(UTC) - timedelta(days=10),
        rule_id="rule_bullish_sweep_v1",
        direction="LONG",
        expiration_date=expiration,
        evidence={},
    )


def _journal(
    decision_id: str,
    candidate_id: str,
    ticker: str,
    *,
    filled: bool = True,
    realized_pnl: float | None = None,
) -> TradeJournalEntry:
    return TradeJournalEntry(
        decision_id=decision_id,
        signal_id=f"sig_{decision_id}",
        candidate_id=candidate_id,
        ticker=ticker,
        direction="LONG",
        client_order_id=f"orion_{decision_id}",
        broker_order_id=f"broker_{decision_id}",
        filled_qty=3.0 if filled else None,
        filled_avg_price=1.10 if filled else None,
        filled_at_utc=datetime.now(UTC) - timedelta(days=9) if filled else None,
        realized_pnl=realized_pnl,
    )


@pytest.mark.asyncio
async def test_expired_entry_is_realized_at_full_premium_loss():
    await init_db()
    expired = datetime.now(UTC) - timedelta(days=3)

    async with async_session_factory() as session:
        session.add(_candidate("cand_exp", "IONQ", expired))
        session.add(_journal("dec_exp", "cand_exp", "IONQ"))
        await session.commit()

    count = await realize_expired_journal_rows()
    assert count == 1

    async with async_session_factory() as session:
        row = (
            (await session.execute(select(TradeJournalEntry).where(TradeJournalEntry.ticker == "IONQ")))
            .scalars()
            .first()
        )
    assert row is not None
    # 3 contracts * 1.10 * 100 = 330 total premium lost.
    assert row.realized_pnl == pytest.approx(-330.0)
    assert row.notes == "expired_worthless"
    assert row.exit_filled_at_utc is not None


@pytest.mark.asyncio
async def test_unexpired_and_already_closed_rows_untouched():
    await init_db()
    live_expiry = datetime.now(UTC) + timedelta(days=5)
    expired = datetime.now(UTC) - timedelta(days=3)

    async with async_session_factory() as session:
        # Still-live option: must not be realized.
        session.add(_candidate("cand_live", "SPY", live_expiry))
        session.add(_journal("dec_live", "cand_live", "SPY"))
        # Already closed by a real fill: must not be overwritten.
        session.add(_candidate("cand_closed", "QQQ", expired))
        session.add(_journal("dec_closed", "cand_closed", "QQQ", realized_pnl=42.0))
        await session.commit()

    count = await realize_expired_journal_rows()
    assert count == 0

    async with async_session_factory() as session:
        live = (
            (await session.execute(select(TradeJournalEntry).where(TradeJournalEntry.ticker == "SPY")))
            .scalars()
            .first()
        )
        closed = (
            (await session.execute(select(TradeJournalEntry).where(TradeJournalEntry.ticker == "QQQ")))
            .scalars()
            .first()
        )
    assert live is not None and live.realized_pnl is None
    assert closed is not None and closed.realized_pnl == pytest.approx(42.0)


@pytest.mark.asyncio
async def test_grace_day_and_unfilled_entries_skipped():
    await init_db()
    # Expired YESTERDAY: inside the settlement grace window — not swept yet.
    yesterday = datetime.now(UTC) - timedelta(days=1)
    long_expired = datetime.now(UTC) - timedelta(days=4)

    async with async_session_factory() as session:
        session.add(_candidate("cand_grace", "IWM", yesterday))
        session.add(_journal("dec_grace", "cand_grace", "IWM"))
        # Entry order placed but never filled: no position, nothing to realize.
        session.add(_candidate("cand_nofill", "GLD", long_expired))
        session.add(_journal("dec_nofill", "cand_nofill", "GLD", filled=False))
        await session.commit()

    count = await realize_expired_journal_rows()
    assert count == 0
