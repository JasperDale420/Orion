"""One-off repair of journal lots wrongly swept as expired_worthless on 2026-08-13.

The rows named in the sweep manifest had in fact been SOLD (their sell fills are
in `fills`, OCC-keyed, and were never attributed because of the OCC/underlying
mismatch). The repair resets exactly those rows, re-runs the fill reconcile and
the remainder-aware sweep, and reports per-row before/after — all in ONE
transaction, rolled back unless ``--apply`` is given.
"""

import os

os.environ["DB_URL"] = "sqlite+aiosqlite:///:memory:"

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from orion.storage.db import async_session_factory, init_db
from orion.storage.models_execution import FillRecord
from orion.storage.models_gold import CandidateTrade
from orion.storage.models_trade_journal import TradeJournalEntry
from orion.jobs.repair_journal_expired_worthless import run_repair

pytestmark = pytest.mark.unit

ENTRY_AT = datetime(2026, 7, 7, 14, 38, tzinfo=UTC)
EXPIRED = datetime(2026, 7, 8, tzinfo=UTC)


async def _seed(*, sold: bool, manifest: Path) -> None:
    await init_db()
    async with async_session_factory() as session:
        session.add(
            CandidateTrade(
                candidate_id="c_nvda",
                ticker="NVDA",
                timestamp_utc=ENTRY_AT,
                rule_id="rule_short_swing_v2",
                direction="LONG",
                option_symbol="NVDA260708C00190000",
                expiration_date=EXPIRED,
                evidence={},
            )
        )
        session.add(
            CandidateTrade(
                candidate_id="c_dust",
                ticker="IONQ",
                timestamp_utc=ENTRY_AT,
                rule_id="rule_swing_v2",
                direction="LONG",
                option_symbol="IONQ260710C00040000",
                expiration_date=datetime(2026, 7, 10, tzinfo=UTC),
                evidence={},
            )
        )
        # Wrongly swept lot (was sold at 4.45).
        session.add(
            TradeJournalEntry(
                decision_id="dec_nvda",
                signal_id="s1",
                candidate_id="c_nvda",
                ticker="NVDA",
                direction="LONG",
                client_order_id="orion_e1",
                broker_order_id="b_e1",
                filled_qty=1,
                filled_avg_price=2.85,
                filled_at_utc=ENTRY_AT,
                exit_filled_at_utc=EXPIRED,
                realized_pnl=-285.0,
                notes="expired_worthless",
                raw_json={},
            )
        )
        # Correctly swept lot (never sold) — must come out unchanged.
        session.add(
            TradeJournalEntry(
                decision_id="dec_dust",
                signal_id="s2",
                candidate_id="c_dust",
                ticker="IONQ",
                direction="LONG",
                client_order_id="orion_e2",
                broker_order_id="b_e2",
                filled_qty=2,
                filled_avg_price=0.50,
                filled_at_utc=ENTRY_AT,
                exit_filled_at_utc=datetime(2026, 7, 10, tzinfo=UTC),
                realized_pnl=-100.0,
                notes="expired_worthless",
                raw_json={},
            )
        )
        # A row NOT in the manifest must never be touched even if it looks similar.
        session.add(
            TradeJournalEntry(
                decision_id="dec_untouched",
                signal_id="s3",
                candidate_id="c_nvda",
                ticker="NVDA",
                direction="LONG",
                client_order_id="orion_e3",
                broker_order_id="b_e3",
                filled_qty=1,
                filled_avg_price=9.99,
                filled_at_utc=ENTRY_AT,
                realized_pnl=-999.0,
                notes="expired_worthless",
                raw_json={},
            )
        )
        if sold:
            session.add(
                FillRecord(
                    id="f_sell",
                    ticker="NVDA260708C00190000",
                    broker_order_id="b_x1",
                    client_order_id="orion_x1",
                    filled_qty=1,
                    filled_avg_price=4.45,
                    side="sell",
                    filled_at_utc=ENTRY_AT + timedelta(hours=1),
                    raw_json={},
                )
            )
        await session.commit()

    with manifest.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["decision_id", "ticker"])
        w.writeheader()
        w.writerow({"decision_id": "dec_nvda", "ticker": "NVDA"})
        w.writerow({"decision_id": "dec_dust", "ticker": "IONQ"})


async def _row(decision_id: str) -> TradeJournalEntry:
    async with async_session_factory() as session:
        row = (
            await session.execute(select(TradeJournalEntry).where(TradeJournalEntry.decision_id == decision_id))
        ).scalar_one()
        session.expunge(row)
        return row


@pytest.mark.asyncio
async def test_dry_run_reports_but_writes_nothing(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    await _seed(sold=True, manifest=manifest)

    report = await run_repair(manifest, apply=False)

    assert report.applied is False
    by_id = {r.decision_id: r for r in report.rows}
    assert by_id["dec_nvda"].before_realized_pnl == pytest.approx(-285.0)
    assert by_id["dec_nvda"].after_realized_pnl == pytest.approx((4.45 - 2.85) * 100)
    assert by_id["dec_nvda"].after_notes == "closed_by=b_x1"
    assert by_id["dec_dust"].after_realized_pnl == pytest.approx(-100.0)
    assert by_id["dec_dust"].after_notes == "expired_worthless"
    assert "dec_untouched" not in by_id

    # Rolled back: DB unchanged.
    row = await _row("dec_nvda")
    assert row.realized_pnl == pytest.approx(-285.0)
    assert row.notes == "expired_worthless"


@pytest.mark.asyncio
async def test_apply_repairs_sold_lot_and_leaves_true_expiry_alone(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    await _seed(sold=True, manifest=manifest)

    report = await run_repair(manifest, apply=True)
    assert report.applied is True

    nvda = await _row("dec_nvda")
    assert nvda.realized_pnl == pytest.approx((4.45 - 2.85) * 100)
    assert nvda.notes == "closed_by=b_x1"
    assert nvda.exit_filled_qty == pytest.approx(1)
    assert nvda.exit_filled_avg_price == pytest.approx(4.45)
    assert nvda.exit_broker_order_id == "b_x1"
    assert nvda.raw_json["exit_allocations"][0]["source"] == "eod_reconcile"

    dust = await _row("dec_dust")
    assert dust.realized_pnl == pytest.approx(-100.0)
    assert dust.notes == "expired_worthless"
    assert dust.exit_filled_qty == pytest.approx(2)

    untouched = await _row("dec_untouched")
    assert untouched.realized_pnl == pytest.approx(-999.0)

    # Second run is a no-op (rows no longer carry the swept marker).
    again = await run_repair(manifest, apply=True)
    assert again.rows == [] or all(r.before_realized_pnl == r.after_realized_pnl for r in again.rows)


@pytest.mark.asyncio
async def test_repair_refuses_when_no_sell_fill_and_lot_would_stay_open(tmp_path: Path) -> None:
    """If a manifest row has no sell fill AND is not past expiry+1d, resetting it would
    re-open a lot (and re-count it against the caps); the repair must not apply."""
    manifest = tmp_path / "manifest.csv"
    await _seed(sold=False, manifest=manifest)
    async with async_session_factory() as session:
        cand = (
            await session.execute(select(CandidateTrade).where(CandidateTrade.candidate_id == "c_nvda"))
        ).scalar_one()
        cand.expiration_date = datetime.now(UTC) + timedelta(days=5)
        await session.commit()

    with pytest.raises(RuntimeError, match="would remain open"):
        await run_repair(manifest, apply=True)

    row = await _row("dec_nvda")
    assert row.realized_pnl == pytest.approx(-285.0)
