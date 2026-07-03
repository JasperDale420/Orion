"""Daily journal-vs-fills PnL cross-check + per-solver/per-rule attribution.

WHY (redesign R5 / task RB.3 — measurement before evolution):
The trade journal's realized-PnL write-back (``execution/persistence.py
::persist_realized_pnl_to_journal``) has existed since 2026-06-08 but has NEVER
been verified against an independent source. Nothing — not solver demotion, not
meta evolution — may act on journal PnL until it reconciles. This job is that
gate. It produces RECOMMENDATIONS ONLY: it persists measurements and (via the
EOD report) surfaces demotion *candidates*. It changes ZERO solver stage flags.

BROKER-TRUTH SOURCE — orion's OWN fills lot book (O8 redesign):
  The shared Alpaca paper account exposes NO per-orion realized-PnL surface:
  portfolio-history is account-level and contaminated by sibling systems
  (3Roses/Cerberus/Kairos/Orbit/WhaleHunter), and Alpaca has no closed-position
  realized-PnL endpoint at all. So the broker side is reconstructed from the one
  per-orion-clean ledger Orion already owns: its ``fills`` table
  (``storage/models_execution.py::FillRecord``), written for EVERY fill — entry
  AND exit — by ``execution/fill_processor.py``. Each row carries
  ``client_order_id`` (orion-attributable by the ``orion_`` prefix), ``ticker``,
  ``side``, ``filled_qty``, ``filled_avg_price``, and ``filled_at_utc``.

  ALGORITHM — per-symbol FIFO lot book:
    Load all orion-owned fills with ``filled_at_utc`` at-or-before end-of-day
    ``d``, ordered ascending. Replay them per symbol into a FIFO lot book: a BUY
    opens/adds a lot at its fill price; a SELL closes lots oldest-first,
    realizing ``(exit_price - entry_price) * qty * multiplier`` per closed lot.
    (Orion is long-options — entries BUY, exits SELL — but both directions are
    handled generally: a SELL with no open long lots opens a short lot, and a
    subsequent BUY closes it realizing ``(entry_price - cover_price) * qty``.)
    The MULTIPLIER is 100 for OCC option symbols (``is_occ_option_symbol``) and
    1 for equity.

  REALIZATION-DAY RULE: a realization is attributed to the day of the CLOSING
  fill (the leg that consumed the lot). ``broker_total`` for day ``d`` = SUM of
  realizations whose closing fill's ``filled_at_utc`` falls on day ``d``. This
  is the O8 fix: a multi-day SWING/POSITION close realizes
  ``proceeds - entry_cost`` against the actual earlier entry, NOT same-day
  signed cashflow (which over-counted a multi-day close as full proceeds).

  COMPLETE-SEED RULE: the lot book is seeded from the ENTIRE orion fills history
  up to end-of-day ``d`` — NOT a finite lookback window. A finite lookback was
  unsound as a *trusted* FIFO seed: if a symbol's true oldest open lot predated
  the window while a newer same-symbol lot sat inside it, a day-``d`` close would
  consume the newer lot (wrong cost basis) and report a TRUSTED-but-wrong total
  instead of routing to ``BROKER_UNAVAILABLE`` (round-4 adversarial finding).
  Loading all fills makes FIFO consume the correct oldest lot. The orion fills
  ledger is small (one system's own fills), so a full-history read is cheap. A
  day-``d`` close that STILL exhausts the open lots (an entry Orion never
  recorded, either direction) is an "unbasis-able" close: we do NOT silently
  count it — the symbol is flagged in ``details`` and ANY unbasis-able day-``d``
  close routes the whole day to ``BROKER_UNAVAILABLE`` (a partial reconstruction
  is not trusted — see O1/O8 discipline below).

  INDEPENDENT CROSS-CHECK FRAMING: both sides now derive from orion's own data —
  the journal side via the risk-manager-derived ``realized_pnl`` write-back, the
  broker side via a raw replay of the fills ledger. This is an INTERNAL
  cross-check: it catches journal-derivation bugs (e.g. the lossy ticker-only
  oldest-open match, or entry-pointer overwrites) by re-deriving PnL straight
  from raw fills. It does NOT catch a broker fill Orion never recorded — fills
  the execution path missed are invisible to both sides; that gap is a separate
  concern, monitored by the execution/position-monitor reconciler, not here.

WHAT IT DOES, for a given trading day (UTC calendar day):
  1. Journal total: SUM(realized_pnl) over orion-attributed closed journal rows
     (``client_order_id LIKE 'orion_%'`` AND realized_pnl IS NOT NULL).
  2. Broker total: replay the orion fills lot book (above) and sum day-``d``
     realizations.
  3. drift = journal_total - broker_total; status = MATCH within tolerance,
     MISMATCH beyond it, or NO_DATA when neither side has orion trades.
  4. Per-solver and per-rule realized-PnL rollups from the journal.

BROKER UNAVAILABILITY (RB.3 O1): a fills-table READ failure (DB outage) is
  NEVER coerced to an empty result — that would make an outage indistinguishable
  from a genuinely-empty broker day and silently "succeed" against nothing. A
  read failure OR any unbasis-able day-``d`` close yields status
  ``BROKER_UNAVAILABLE``: the broker side is untrusted, attribution + demotion
  candidates are SUPPRESSED, the reason is persisted into ``details``, and a
  Discord alert fires. ``NO_DATA`` stays reserved for a genuinely-empty fills
  ledger on day ``d``.

  Further limitations, persisted into ``details`` for the operator:
    - A symbol left non-flat (net qty != 0) at day end has open exposure but a
      defined realized component; we still flag it in ``details`` so a mismatch
      driven by an overnight hold is explainable.
    - Unbasis-able symbols (a day-``d`` close that exhausts the open lots — an
      entry Orion never recorded, either direction) are listed in ``details``
      and force ``BROKER_UNAVAILABLE``.
    - A still-``partially_filled`` orion order forces ``BROKER_UNAVAILABLE``:
      ``FillRecord`` is ONE cumulative row per order (overwritten with blended
      price / latest timestamp on each partial update), so until the order
      completes, the day's split is not final — a GTC exit partially filled
      today that completes tomorrow would otherwise be double-counted.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select

from orion.execution.attribution import (
    is_occ_option_symbol,
    is_orion_owned,
    orion_order_id_sql_pattern,
)
from orion.shared.db_utils import db_query, db_write
from orion.shared.liveness import publish_liveness
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.reconcile_pnl")

# Reconciliation statuses. BROKER_UNAVAILABLE marks an untrusted broker side
# (fills-table read failure OR an unbasis-able close — a day-d exit that exhausts
# the open lots because Orion never recorded the entry) — it is distinct from
# NO_DATA (genuinely-empty fills ledger on day d).
STATUS_MATCH = "MATCH"
STATUS_MISMATCH = "MISMATCH"
STATUS_NO_DATA = "NO_DATA"
STATUS_BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"

ALERT_DEDUPE_BROKER_UNAVAILABLE = "pnl_reconcile_broker_unavailable"

# Per-trade rounding allowance. Drift within (tolerance * max(trade counts)) is
# treated as floating-point/rounding noise rather than a real mismatch.
# Configurable via env so ops can widen it without a code change.
DEFAULT_TOLERANCE_USD = 0.01

# Option contract multiplier — realized PnL per contract is price-delta * 100.
_OPTION_MULTIPLIER = 100.0

# This job runs once per trading day inside the EOD run; declare a generous
# budget so the dead-man watchdog does not page on a non-trading day.
LIVENESS_CADENCE_BUDGET_SECONDS = 36 * 60 * 60


def _tolerance_usd() -> float:
    raw = os.getenv("ORION_RECONCILE_TOLERANCE_USD")
    if raw is None:
        return DEFAULT_TOLERANCE_USD
    try:
        return abs(float(raw))
    except (TypeError, ValueError):
        logger.warning("reconcile_bad_tolerance_env", value=raw)
        return DEFAULT_TOLERANCE_USD


def _day_bounds_utc(d: date) -> tuple[datetime, datetime]:
    start = datetime.combine(d, datetime.min.time()).replace(tzinfo=UTC)
    return start, start + timedelta(days=1)


def _parse_ts(value: Any) -> datetime | None:
    """Parse an Alpaca timestamp (ISO-8601 string or datetime) to tz-aware UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


@dataclass
class BrokerReconstruction:
    """Realized PnL reconstructed from orion's own fills lot book."""

    total: float
    order_count: int
    per_symbol: dict[str, float]
    non_flat_symbols: list[str] = field(default_factory=list)
    # Symbols with a day-``d`` close that could not be cost-based against an open
    # lot within the lookback (entry predates the lookback, or fills missing).
    # Non-empty ⇒ the day routes to BROKER_UNAVAILABLE (partial reconstruction
    # is not trusted).
    unbasis_symbols: list[str] = field(default_factory=list)


# A single open lot: remaining quantity and the price it was opened at. ``sign``
# is +1 for a long lot (opened by a BUY), -1 for a short lot (opened by a SELL).
@dataclass
class _Lot:
    qty: float
    price: float
    sign: int


def reconstruct_broker_realized_pnl(fills: list[dict[str, Any]], d: date) -> BrokerReconstruction:
    """Reconstruct day-``d`` realized PnL from a per-symbol FIFO lot book.

    Pure function (no I/O) so it is unit-testable against canned fill dicts.
    ``fills`` is the orion-owned fill ledger seeded from the lookback up to
    end-of-day ``d``, each dict carrying ``client_order_id``, ``ticker`` (the
    full OCC contract for options), ``side``, ``filled_qty``, ``filled_avg_price``
    and ``filled_at_utc``. Fills are sorted by ``filled_at_utc`` ascending before
    replay (callers that load from the DB already order ascending; this re-sort
    keeps the pure function order-independent).

    LOT BOOK (FIFO). Per symbol, a BUY opens or extends a long lot; a SELL closes
    open long lots oldest-first, realizing ``(sell_price - lot_price) * qty *
    multiplier`` per closed lot. The opposite direction is handled symmetrically
    (a SELL with no long lots opens a short lot; a later BUY covers it realizing
    ``(lot_price - buy_price) * qty * multiplier``). MULTIPLIER = 100 for OCC
    option symbols, else 1.

    REALIZATION DAY. A realization is attributed to the CLOSING fill's day. Only
    realizations whose closing fill ``filled_at_utc`` ∈ day ``d`` contribute to
    ``total`` / ``per_symbol`` / ``order_count`` (``order_count`` = number of
    day-``d`` closing fills that realized PnL). Earlier fills only seed the lot
    book.

    UNBASIS-ABLE CLOSE. A day-``d`` close that consumes more quantity than the
    open lots carry (the entry predates the lookback, or fills are missing) is
    recorded in ``unbasis_symbols`` rather than counted; any such symbol forces
    the caller to ``BROKER_UNAVAILABLE``.
    """
    start, end = _day_bounds_utc(d)
    books: dict[str, deque[_Lot]] = defaultdict(deque)
    per_symbol_realized: dict[str, float] = defaultdict(float)
    order_count = 0
    unbasis: set[str] = set()

    ordered = sorted(fills, key=lambda f: _parse_ts(f.get("filled_at_utc")) or datetime.min.replace(tzinfo=UTC))

    for f in ordered:
        if not is_orion_owned(f.get("client_order_id")):
            continue
        qty = _to_float(f.get("filled_qty")) or 0.0
        price = _to_float(f.get("filled_avg_price"))
        if qty <= 0 or price is None:
            continue
        side = str(f.get("side") or "").lower()
        if side == "buy":
            open_sign, close_sign = 1, -1
        elif side == "sell":
            open_sign, close_sign = -1, 1
        else:
            continue

        symbol = str(f.get("ticker") or "")
        filled_at = _parse_ts(f.get("filled_at_utc"))
        is_day_d = filled_at is not None and start <= filled_at < end
        multiplier = _OPTION_MULTIPLIER if is_occ_option_symbol(symbol) else 1.0
        book = books[symbol]

        remaining = qty
        realized_today = 0.0
        closed_any = False
        # Close opposite-sign lots oldest-first.
        while remaining > 1e-9 and book and book[0].sign == close_sign:
            closed_any = True
            lot = book[0]
            take = min(remaining, lot.qty)
            # Long lot closed by a sell: (sell - entry); short lot closed by a
            # buy: (entry - buy). ``lot.sign`` distinguishes the two.
            if lot.sign == 1:
                realized_today += (price - lot.price) * take * multiplier
            else:
                realized_today += (lot.price - price) * take * multiplier
            lot.qty -= take
            remaining -= take
            if lot.qty <= 1e-9:
                book.popleft()

        if remaining > 1e-9:
            # Leftover quantity opens a lot in this fill's direction (FIFO order
            # preserved by appending). A day-``d`` fill leaves an unbasis-able
            # close when it acted as a close but ran out of basis:
            #   - ``closed_any``: it consumed some opposite lots then overflowed
            #     (a short over-cover by a BUY, or a long over-sell by a SELL) —
            #     the missing basis exists in EITHER direction (round-4 symmetry
            #     fix; the old guard caught only SELLs).
            #   - a SELL that closed NOTHING (no long lots at all): Orion is
            #     long-biased, so a naked sell means the long entry was never
            #     recorded. (A BUY that closed nothing is a normal long entry —
            #     NOT unbasis — so it is deliberately excluded.)
            # Over-flagging a legitimate position flip is acceptable: the safe
            # failure mode is BROKER_UNAVAILABLE, never a trusted-but-wrong total.
            if is_day_d and (closed_any or side == "sell"):
                unbasis.add(symbol)
            book.append(_Lot(qty=remaining, price=price, sign=open_sign))

        if is_day_d and abs(realized_today) > 0.0:
            per_symbol_realized[symbol] += realized_today
            order_count += 1

    non_flat = sorted(s for s, lots in books.items() if sum(lot.qty for lot in lots) > 1e-9)
    total = round(sum(per_symbol_realized.values()), 6)
    return BrokerReconstruction(
        total=total,
        order_count=order_count,
        per_symbol={s: round(v, 6) for s, v in per_symbol_realized.items()},
        non_flat_symbols=non_flat,
        unbasis_symbols=sorted(unbasis),
    )


@dataclass
class JournalTotals:
    total: float
    trade_count: int
    win_count: int


@dataclass
class AttributionRow:
    key: str
    n_trades: int
    realized_pnl_total: float
    win_count: int

    @property
    def expectancy(self) -> float | None:
        return round(self.realized_pnl_total / self.n_trades, 6) if self.n_trades else None


async def _load_journal_closed_trades(d: date) -> list[dict[str, Any]]:
    """Return orion-attributed CLOSED journal rows for day ``d``.

    Closed = realized_pnl IS NOT NULL (the write-back only sets it on a closing
    fill). The day is keyed on ``filled_at_utc`` (when the close realized), with
    a fallback to ``created_at_utc`` for rows whose filled_at was never stamped.
    Each row is returned with its joined ``rule_id`` from candidate_trades.

    JOIN PATH (cited from models): TradeJournalEntry.candidate_id ->
    CandidateTrade.candidate_id -> CandidateTrade.rule_id. solver_id is on the
    journal row directly (TradeJournalEntry.solver_id).
    """
    from orion.storage.models_gold import CandidateTrade
    from orion.storage.models_trade_journal import TradeJournalEntry

    start, end = _day_bounds_utc(d)

    async def query_with_day(session: Any) -> list[dict[str, Any]]:
        stmt = (
            select(
                TradeJournalEntry.decision_id,
                TradeJournalEntry.solver_id,
                TradeJournalEntry.realized_pnl,
                TradeJournalEntry.client_order_id,
                TradeJournalEntry.exit_filled_at_utc,
                TradeJournalEntry.filled_at_utc,
                TradeJournalEntry.created_at_utc,
                CandidateTrade.rule_id,
            )
            .outerjoin(CandidateTrade, TradeJournalEntry.candidate_id == CandidateTrade.candidate_id)
            .where(
                TradeJournalEntry.client_order_id.like("orion_%"),
                TradeJournalEntry.realized_pnl.isnot(None),
                # Expired-worthless realizations have no broker fill (an
                # option removed at expiry produces no fill activity), so
                # including them would force a false journal-vs-broker
                # MISMATCH on the expiry day. Their losses are audited
                # directly from the journal (notes='expired_worthless').
                or_(
                    TradeJournalEntry.notes.is_(None),
                    TradeJournalEntry.notes != "expired_worthless",
                ),
            )
        )
        rows = (await session.execute(stmt)).all()
        out: list[dict[str, Any]] = []
        for (
            decision_id,
            solver_id,
            realized_pnl,
            client_order_id,
            exit_filled_at,
            filled_at,
            created_at,
            rule_id,
        ) in rows:
            # Realization day = the CLOSING fill's day, matching the lot-book's
            # realization-day rule. Since the B3 entry-preservation fix,
            # ``filled_at_utc`` keeps the ENTRY fill time on a closed row and the
            # close timestamp lives in ``exit_filled_at_utc`` — bucketing by entry
            # time would put a multi-day hold's PnL on the WRONG day and force a
            # false MISMATCH (round-6 adversarial finding). Fall back to
            # ``filled_at_utc``/``created_at`` only for rows predating the
            # exit-leg columns.
            effective = exit_filled_at or filled_at or created_at
            if effective is None:
                continue
            eff = effective if effective.tzinfo else effective.replace(tzinfo=UTC)
            if not (start <= eff < end):
                continue
            out.append(
                {
                    "decision_id": decision_id,
                    "solver_id": solver_id or "__unknown__",
                    "rule_id": rule_id or "__unknown__",
                    "realized_pnl": float(realized_pnl),
                    "client_order_id": client_order_id,
                }
            )
        return out

    return await db_query(query_with_day)


def _summarize_journal(rows: list[dict[str, Any]]) -> JournalTotals:
    total = round(sum(r["realized_pnl"] for r in rows), 6)
    wins = sum(1 for r in rows if r["realized_pnl"] > 0)
    return JournalTotals(total=total, trade_count=len(rows), win_count=wins)


def _rollup(rows: list[dict[str, Any]], key: str) -> list[AttributionRow]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        grouped[str(r[key])].append(r["realized_pnl"])
    out: list[AttributionRow] = []
    for k, pnls in grouped.items():
        out.append(
            AttributionRow(
                key=k,
                n_trades=len(pnls),
                realized_pnl_total=round(sum(pnls), 6),
                win_count=sum(1 for p in pnls if p > 0),
            )
        )
    return sorted(out, key=lambda a: a.realized_pnl_total)


def _classify(
    journal: JournalTotals, broker: BrokerReconstruction, tolerance_usd: float
) -> tuple[str, float, float | None]:
    """Return (status, drift_abs, drift_pct). Broker side is assumed trusted here."""
    if journal.trade_count == 0 and broker.order_count == 0:
        return STATUS_NO_DATA, 0.0, None
    drift = journal.total - broker.total
    drift_abs = round(abs(drift), 6)
    # Tolerance scales with the number of trades (per-trade rounding allowance).
    n = max(journal.trade_count, 1)
    allowed = tolerance_usd * n
    status = STATUS_MATCH if drift_abs <= allowed else STATUS_MISMATCH
    denom = abs(broker.total) if abs(broker.total) > 1e-9 else None
    drift_pct = round(drift / denom * 100.0, 4) if denom else None
    return status, drift_abs, drift_pct


@dataclass
class ReconcileResult:
    trade_date: date
    journal: JournalTotals
    broker: BrokerReconstruction
    status: str
    drift_abs: float
    drift_pct: float | None
    tolerance_usd: float
    solver_rows: list[AttributionRow]
    rule_rows: list[AttributionRow]
    consecutive_mismatch_days: int = 1
    broker_unavailable_reason: str | None = None

    @property
    def broker_trusted(self) -> bool:
        """False when the broker side is untrusted (BROKER_UNAVAILABLE)."""
        return self.status != STATUS_BROKER_UNAVAILABLE

    def details(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "journal_trade_count": self.journal.trade_count,
            "journal_win_count": self.journal.win_count,
            "broker_order_count": self.broker.order_count,
            "broker_per_symbol": self.broker.per_symbol,
            "broker_non_flat_symbols": self.broker.non_flat_symbols,
            "broker_unbasis_symbols": self.broker.unbasis_symbols,
            "tolerance_usd": self.tolerance_usd,
            "consecutive_mismatch_days": self.consecutive_mismatch_days,
        }
        if self.broker_unavailable_reason is not None:
            d["broker_unavailable_reason"] = self.broker_unavailable_reason
            d["broker_trusted"] = False
        return d


async def _count_consecutive_mismatch_days(d: date, current_status: str) -> int:
    """How many consecutive trading days up to and including ``d`` are MISMATCH.

    Reads prior persisted reconciliation rows. Returns 1 when today is the first
    mismatch (or 0 when today is not a mismatch). Used to drive the 2+
    consecutive-day Discord escalation.
    """
    if current_status != "MISMATCH":
        return 0
    from orion.storage.models_attribution import PnlReconciliation

    async def query(session: Any) -> list[tuple[date, str]]:
        stmt = (
            select(PnlReconciliation.trade_date, PnlReconciliation.status)
            .where(PnlReconciliation.trade_date < d)
            .order_by(PnlReconciliation.trade_date.desc())
            .limit(30)
        )
        return [(r[0], r[1]) for r in (await session.execute(stmt)).all()]

    try:
        prior = await db_query(query)
    except Exception:
        logger.warning("reconcile_consecutive_lookup_failed", exc_info=True)
        return 1

    streak = 1
    for _row_date, status in prior:
        if status == "MISMATCH":
            streak += 1
        else:
            break
    return streak


def _broker_unavailable_result(d: date, journal: JournalTotals, tolerance: float, reason: str) -> ReconcileResult:
    """Build an untrusted-broker result: status BROKER_UNAVAILABLE, broker side
    zeroed, attribution + demotion candidates SUPPRESSED (empty rollups). The
    journal totals are kept for the record; the broker side is explicitly NOT
    reconciled against. ``reason`` lands in ``details`` for the operator.
    """
    return ReconcileResult(
        trade_date=d,
        journal=journal,
        broker=BrokerReconstruction(total=0.0, order_count=0, per_symbol={}),
        status=STATUS_BROKER_UNAVAILABLE,
        drift_abs=0.0,
        drift_pct=None,
        tolerance_usd=tolerance,
        solver_rows=[],  # untrusted → suppress attribution
        rule_rows=[],  # untrusted → suppress attribution
        consecutive_mismatch_days=0,
        broker_unavailable_reason=reason,
    )


async def _load_orion_fills(d: date) -> list[dict[str, Any]]:
    """Load the ENTIRE orion-owned fills history up to end-of-day ``d``.

    Reads ``FillRecord`` rows whose ``client_order_id`` is orion-attributed
    (``LIKE 'orion_%'``) and whose ``filled_at_utc`` is before end-of-day ``d``.
    No finite lookback floor: a finite window could silently drop a symbol's true
    oldest open lot and let a day-``d`` close consume a newer lot at the wrong
    cost basis (round-4 adversarial finding). Loading all fills makes the FIFO
    replay consume the correct oldest lot, or — if the entry was never recorded —
    leave the close unbasis-able (→ ``BROKER_UNAVAILABLE``). The orion ledger is
    one system's own fills, so a full-history read is cheap. Rows with a NULL
    ``filled_at_utc`` are skipped (no realization instant). Returned ascending by
    ``filled_at_utc`` for deterministic replay.
    """
    from orion.storage.models_execution import FillRecord

    _, day_end = _day_bounds_utc(d)

    async def query(session: Any) -> list[dict[str, Any]]:
        stmt = (
            select(
                FillRecord.client_order_id,
                FillRecord.ticker,
                FillRecord.side,
                FillRecord.filled_qty,
                FillRecord.filled_avg_price,
                FillRecord.filled_at_utc,
            )
            .where(
                FillRecord.client_order_id.like(orion_order_id_sql_pattern()),
                FillRecord.filled_at_utc.isnot(None),
                FillRecord.filled_at_utc < day_end,
            )
            .order_by(FillRecord.filled_at_utc.asc())
        )
        rows = (await session.execute(stmt)).all()
        out: list[dict[str, Any]] = []
        for client_order_id, ticker, side, filled_qty, filled_avg_price, filled_at in rows:
            out.append(
                {
                    "client_order_id": client_order_id,
                    "ticker": ticker,
                    "side": side,
                    "filled_qty": filled_qty,
                    "filled_avg_price": filled_avg_price,
                    "filled_at_utc": filled_at,
                }
            )
        return out

    return await db_query(query)


async def _pending_partial_fill_tickers() -> list[str]:
    """Tickers of orion-attributed orders currently in ``partially_filled`` status.

    ``persist_fill_record`` keeps ONE ``FillRecord`` row per ``broker_order_id``,
    OVERWRITTEN with cumulative qty / blended price / latest timestamp on each
    partial-fill update (round-5 adversarial finding). While an order is still
    partially filled, today's row is NOT final: a GTC bracket exit partially
    filled today that completes tomorrow will retroactively morph this row onto
    tomorrow (full qty, blended price) — so a total computed from it today would
    double-count once tomorrow's run re-replays the morphed row. Any pending
    partial therefore makes the day's split provably non-final → the caller
    routes to ``BROKER_UNAVAILABLE``. Once the order completes (or is canceled
    after a partial), the row is stable and both the journal and fills sides
    attribute the whole realization to the completion day consistently.
    """
    from orion.storage.models_execution import OrderRecord

    async def query(session: Any) -> list[str]:
        stmt = (
            select(OrderRecord.ticker)
            .where(
                OrderRecord.client_order_id.like(orion_order_id_sql_pattern()),
                OrderRecord.status == "partially_filled",
            )
            .distinct()
        )
        return sorted((await session.execute(stmt)).scalars().all())

    return await db_query(query)


async def compute_reconciliation(d: date) -> ReconcileResult:
    """Compute (but do NOT persist) the reconciliation + attribution for day ``d``.

    Both sides derive from orion's own data: the journal side from the
    risk-manager-derived ``realized_pnl`` write-back, the broker side from a FIFO
    lot-book replay of the ``fills`` table (orion-attributed). This is an
    INDEPENDENT internal cross-check (catches journal-derivation bugs; does NOT
    catch broker fills orion never recorded — see module docstring).
    """
    tolerance = _tolerance_usd()
    journal_rows = await _load_journal_closed_trades(d)
    journal = _summarize_journal(journal_rows)
    solver_rows = _rollup(journal_rows, "solver_id")
    rule_rows = _rollup(journal_rows, "rule_id")

    try:
        fills = await _load_orion_fills(d)
        pending_partial = await _pending_partial_fill_tickers()
    except Exception as exc:
        # O1: a fills-table READ failure is NOT coerced to empty — that would
        # make a DB outage indistinguishable from a real empty broker day.
        logger.error("reconcile_fills_read_failed", error=str(exc), exc_info=True)
        return _broker_unavailable_result(d, journal, tolerance, f"fills read failed: {exc}")

    if pending_partial:
        # Round-5: a still-partially-filled orion order means its cumulative
        # FillRecord row will morph when the order completes — today's split is
        # not final and must not be trusted (see _pending_partial_fill_tickers).
        logger.error(
            "reconcile_pending_partial_fills",
            trade_date=str(d),
            tickers=pending_partial,
        )
        return _broker_unavailable_result(
            d,
            journal,
            tolerance,
            f"pending partially-filled order(s) — day split not final: {', '.join(pending_partial)}",
        )

    broker = reconstruct_broker_realized_pnl(fills, d)

    if broker.unbasis_symbols:
        # O8/O1: a day-d close that exhausted the open lots — Orion never recorded
        # the entry (either direction). A partial reconstruction is not trusted;
        # route the whole day to untrusted.
        logger.error(
            "reconcile_broker_unbasis_close",
            trade_date=str(d),
            symbols=broker.unbasis_symbols,
        )
        return _broker_unavailable_result(
            d,
            journal,
            tolerance,
            f"unbasis-able close(s) (entry never recorded in orion fills): {', '.join(broker.unbasis_symbols)}",
        )

    status, drift_abs, drift_pct = _classify(journal, broker, tolerance)
    consecutive = await _count_consecutive_mismatch_days(d, status)

    return ReconcileResult(
        trade_date=d,
        journal=journal,
        broker=broker,
        status=status,
        drift_abs=drift_abs,
        drift_pct=drift_pct,
        tolerance_usd=tolerance,
        solver_rows=solver_rows,
        rule_rows=rule_rows,
        consecutive_mismatch_days=consecutive,
    )


async def persist_reconciliation(result: ReconcileResult) -> None:
    """Upsert the reconciliation row and the per-solver / per-rule rollups."""
    from orion.storage.models_attribution import (
        PnlReconciliation,
        RulePnlAttribution,
        SolverPnlAttribution,
    )

    async def write(session: Any) -> None:
        from sqlalchemy import delete

        await session.merge(
            PnlReconciliation(
                trade_date=result.trade_date,
                journal_total=result.journal.total,
                broker_total=result.broker.total,
                drift_abs=result.drift_abs,
                drift_pct=result.drift_pct,
                journal_trade_count=result.journal.trade_count,
                broker_trade_count=result.broker.order_count,
                tolerance_usd=result.tolerance_usd,
                status=result.status,
                details=result.details(),
            )
        )
        # Replace (not just upsert) this day's attribution: a rerun that flips to
        # BROKER_UNAVAILABLE/untrusted carries empty solver_rows/rule_rows, so a
        # plain merge would leave a PRIOR covered run's rows behind — stale
        # demotion evidence for a day now marked untrusted (adversarial review
        # O1b). Delete the day's rows in the same transaction before re-inserting.
        await session.execute(delete(SolverPnlAttribution).where(SolverPnlAttribution.trade_date == result.trade_date))
        await session.execute(delete(RulePnlAttribution).where(RulePnlAttribution.trade_date == result.trade_date))
        for row in result.solver_rows:
            await session.merge(
                SolverPnlAttribution(
                    trade_date=result.trade_date,
                    solver_id=row.key,
                    n_trades=row.n_trades,
                    realized_pnl_total=row.realized_pnl_total,
                    expectancy=row.expectancy,
                    win_count=row.win_count,
                )
            )
        for row in result.rule_rows:
            await session.merge(
                RulePnlAttribution(
                    trade_date=result.trade_date,
                    rule_id=row.key,
                    n_trades=row.n_trades,
                    realized_pnl_total=row.realized_pnl_total,
                    expectancy=row.expectancy,
                    win_count=row.win_count,
                )
            )

    await db_write(write)


async def _maybe_alert_broker_unavailable(result: ReconcileResult) -> None:
    """Fire a Discord alert when the broker side is untrusted (O1/O2).

    A BROKER_UNAVAILABLE day means reconciliation ran against nothing
    trustworthy (Gateway/auth outage or incomplete shared-account coverage) —
    it must page, not silently "succeed". Reuses the shared alert path with
    dedupe_key='pnl_reconcile_broker_unavailable'.
    """
    if result.status != STATUS_BROKER_UNAVAILABLE:
        return
    from orion.shared.alerts import send_discord_alert

    reason = result.broker_unavailable_reason or "broker truth unavailable"
    msg = (
        f"PnL reconciliation BROKER_UNAVAILABLE ({result.trade_date}): {reason}. "
        "Broker side is untrusted — attribution + demotion candidates SUPPRESSED; "
        "no reconciliation against an incomplete fills reconstruction."
    )
    await send_discord_alert(msg, dedupe_key=ALERT_DEDUPE_BROKER_UNAVAILABLE)


async def run_reconciliation(
    d: date | None = None,
    *,
    publish_liveness_row: bool = False,
) -> ReconcileResult:
    """Compute, persist, and log the daily reconciliation.

    ``publish_liveness_row`` is set True only when this job runs standalone via
    its own launchd entry. When invoked from the EOD run the EOD agent's own
    liveness already covers the work, so the default is False (no duplicate
    service registration in the dead-man watchdog).
    """
    target = d or datetime.now(UTC).date()
    result = await compute_reconciliation(target)
    await persist_reconciliation(result)

    logger.info(
        "pnl_reconciliation_complete",
        trade_date=str(target),
        status=result.status,
        journal_total=result.journal.total,
        broker_total=result.broker.total,
        drift_abs=result.drift_abs,
        drift_pct=result.drift_pct,
        journal_trades=result.journal.trade_count,
        broker_orders=result.broker.order_count,
    )

    await _maybe_alert_broker_unavailable(result)

    if publish_liveness_row:
        await publish_liveness("reconcile_pnl", cadence_budget_seconds=LIVENESS_CADENCE_BUDGET_SECONDS)

    return result


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Daily journal-vs-broker PnL reconciliation (RB.3).")
    p.add_argument("--date", help="Trading day to reconcile (YYYY-MM-DD). Default: today (UTC).")
    return p.parse_args(argv)


async def _main_async(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    target = date.fromisoformat(args.date) if args.date else None
    from orion.storage.db import init_db

    await init_db()
    result = await run_reconciliation(target, publish_liveness_row=True)
    print(  # noqa: T201 — standalone CLI diagnostic output
        f"[reconcile_pnl] {result.trade_date} status={result.status} "
        f"journal={result.journal.total:.2f} broker={result.broker.total:.2f} "
        f"drift_abs={result.drift_abs:.2f} "
        f"(journal n={result.journal.trade_count}, broker n={result.broker.order_count})"
    )


def main(argv: list[str] | None = None) -> None:
    from orion.shared.logger import setup_logging

    setup_logging()
    asyncio.run(_main_async(argv))


if __name__ == "__main__":
    main()
