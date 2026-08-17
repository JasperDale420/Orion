"""Repair journal lots that were swept as ``expired_worthless`` although they had been sold.

Operator command (one-off; safe to re-run):

    DB_URL="postgresql+asyncpg://…@localhost:5440/orion_db" \\
      uv run python -m orion.jobs.repair_journal_expired_worthless \\
      --manifest logs/expired_journal_manifest_20260813.csv [--apply]

Background: until 2026-08-16 the closing-fill → journal write matched on the
OCC contract symbol against a journal ``ticker`` that holds the underlying, so
no sell was ever attributed; the 2026-08-13 expiry sweep then booked every open
July lot at -100% of premium (-$8,801) although the broker fills show they were
sold (≈ -$955). This command, in ONE transaction, for exactly the manifest's
rows that still carry ``notes='expired_worthless'``: clears the swept
realization, re-runs the fill reconcile (sell fills → lots, FIFO, per-lot P&L)
and the remainder-aware expiry sweep, and reports before/after per row. Without
``--apply`` the transaction is rolled back and only the report is printed.

Refuses to apply if any reset lot would remain OPEN afterwards (no sell fill and
not yet past expiry+1d): that would re-count it against the entry caps.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select

from orion.execution.persistence import reconcile_exits_in_session, sweep_expired_in_session
from orion.shared.db_utils import db_transaction
from orion.shared.logger import setup_struct_logger
from orion.storage.models_trade_journal import TradeJournalEntry

logger = setup_struct_logger(__name__)

SWEPT_NOTE = "expired_worthless"


@dataclass(frozen=True)
class RowReport:
    decision_id: str
    ticker: str
    filled_qty: float | None
    filled_avg_price: float | None
    before_realized_pnl: float | None
    after_realized_pnl: float | None
    after_exit_qty: float | None
    after_exit_avg_price: float | None
    after_notes: str | None


@dataclass
class RepairReport:
    applied: bool
    rows: list[RowReport] = field(default_factory=list)

    @property
    def before_total(self) -> float:
        return sum(r.before_realized_pnl or 0.0 for r in self.rows)

    @property
    def after_total(self) -> float:
        return sum(r.after_realized_pnl or 0.0 for r in self.rows)


class _RollbackDryRun(Exception):
    """Raised inside the transaction to roll it back on a dry run."""


def _manifest_ids(manifest: Path) -> list[str]:
    with manifest.open(newline="") as fh:
        ids = [row["decision_id"].strip() for row in csv.DictReader(fh) if row.get("decision_id")]
    if not ids:
        raise RuntimeError(f"manifest {manifest} has no decision_id rows")
    return ids


async def run_repair(manifest: Path, *, apply: bool) -> RepairReport:
    ids = _manifest_ids(manifest)
    report = RepairReport(applied=False)

    async def work(session: Any) -> RepairReport:
        rows = (
            (
                await session.execute(
                    select(TradeJournalEntry)
                    .where(TradeJournalEntry.decision_id.in_(ids), TradeJournalEntry.notes == SWEPT_NOTE)
                    .order_by(TradeJournalEntry.filled_at_utc.asc())
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        before = {r.decision_id: float(r.realized_pnl) if r.realized_pnl is not None else None for r in rows}
        for r in rows:
            r.realized_pnl = None
            r.exit_filled_qty = None
            r.exit_filled_avg_price = None
            r.exit_filled_at_utc = None
            r.exit_broker_order_id = None
            r.notes = None
            raw = dict(r.raw_json) if isinstance(r.raw_json, dict) else {}
            raw.pop("exit_allocations", None)
            r.raw_json = raw
        await session.flush()

        scope = {r.decision_id for r in rows}
        await reconcile_exits_in_session(session, only_decision_ids=scope)
        await sweep_expired_in_session(session, only_decision_ids=scope)
        await session.flush()

        still_open = [r.decision_id for r in rows if r.realized_pnl is None]
        for r in rows:
            report.rows.append(
                RowReport(
                    decision_id=r.decision_id,
                    ticker=r.ticker,
                    filled_qty=r.filled_qty,
                    filled_avg_price=r.filled_avg_price,
                    before_realized_pnl=before[r.decision_id],
                    after_realized_pnl=float(r.realized_pnl) if r.realized_pnl is not None else None,
                    after_exit_qty=r.exit_filled_qty,
                    after_exit_avg_price=r.exit_filled_avg_price,
                    after_notes=r.notes,
                )
            )
        if still_open:
            raise RuntimeError(
                f"{len(still_open)} manifest lot(s) would remain open after repair (no sell fill, not past "
                f"expiry+1d): {still_open}. Nothing was changed."
            )
        if not apply:
            raise _RollbackDryRun()
        return report

    try:
        await db_transaction(work)
    except _RollbackDryRun:
        return report
    report.applied = True
    logger.info(
        "Journal expired-worthless repair applied",
        extra={
            "event_type": "JOURNAL_EXPIRED_REPAIR_APPLIED",
            "rows": len(report.rows),
            "before_total": report.before_total,
            "after_total": report.after_total,
        },
    )
    return report


def _print(report: RepairReport) -> None:
    hdr = f"{'decision_id':<20} {'tkr':<5} {'qty':>4} {'entry':>7} {'before':>9} {'after':>9} {'exit_qty':>8} {'exit_px':>7}  notes"
    print(hdr)
    print("-" * len(hdr))
    for r in report.rows:
        print(
            f"{r.decision_id[:20]:<20} {r.ticker:<5} {r.filled_qty or 0:>4.0f} {r.filled_avg_price or 0:>7.2f} "
            f"{r.before_realized_pnl or 0:>9.2f} {r.after_realized_pnl or 0:>9.2f} "
            f"{r.after_exit_qty or 0:>8.2f} {r.after_exit_avg_price or 0:>7.2f}  {r.after_notes}"
        )
    print("-" * len(hdr))
    print(f"rows={len(report.rows)} before_total={report.before_total:.2f} after_total={report.after_total:.2f}")
    print("APPLIED" if report.applied else "DRY RUN — nothing written (pass --apply to commit)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, required=True, help="CSV with a decision_id column")
    parser.add_argument("--apply", action="store_true", help="commit the repair (default: dry run)")
    args = parser.parse_args(argv)
    report = asyncio.run(run_repair(args.manifest, apply=args.apply))
    _print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
