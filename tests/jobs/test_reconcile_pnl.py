"""Tests for the journal-vs-fills PnL reconciliation job (RB.3).

Covers the pure fills-lot-book broker-PnL reconstruction (FIFO, multiplier,
realization-day rule, lookback/unbasis handling), the journal/attribution
rollups against the in-memory DB, status classification + tolerance, the
orion-only attribution filter (shared-account contamination guard), and
persistence (including the O1b stale-attribution clear).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from orion.jobs import reconcile_pnl
from orion.jobs.reconcile_pnl import (
    STATUS_BROKER_UNAVAILABLE,
    AttributionRow,
    BrokerReconstruction,
    JournalTotals,
    _broker_unavailable_result,
    _classify,
    _rollup,
    _summarize_journal,
    compute_reconciliation,
    persist_reconciliation,
    reconstruct_broker_realized_pnl,
    run_reconciliation,
)
from orion.shared.db_utils import db_query, db_write
from orion.storage.models_attribution import (
    PnlReconciliation,
    RulePnlAttribution,
    SolverPnlAttribution,
)
from orion.storage.models_execution import FillRecord
from orion.storage.models_gold import CandidateTrade
from orion.storage.models_trade_journal import TradeJournalEntry

TODAY = date(2026, 6, 11)
SYM = "AAPL260619C00200000"


def _ts(day: int = 11, hour: int = 15) -> datetime:
    return datetime(2026, 6, day, hour, 0, tzinfo=UTC)


def _fill(
    *,
    side: str,
    qty: str,
    price: str | None,
    ticker: str = SYM,
    client_order_id: str = "orion_x",
    filled_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "client_order_id": client_order_id,
        "ticker": ticker,
        "side": side,
        "filled_qty": qty,
        "filled_avg_price": price,
        "filled_at_utc": filled_at if filled_at is not None else _ts(),
    }


# ───────────────────────── pure lot-book reconstruction ─────────────────────────


def test_reconstruct_skips_non_orion_fills():
    """Shared-account guard: only orion_-prefixed client_order_ids count."""
    fills = [
        _fill(side="sell", qty="1", price="5.00", client_order_id="cerberus_abc"),
    ]
    out = reconstruct_broker_realized_pnl(fills, TODAY)
    assert out.total == 0.0
    assert out.order_count == 0


def test_reconstruct_option_round_trip_realized_pnl():
    """Buy 1 @ $2.00, sell 1 @ $3.00 → +$100 (×100 multiplier), flat."""
    fills = [
        _fill(side="buy", qty="1", price="2.00", filled_at=_ts(hour=14)),
        _fill(side="sell", qty="1", price="3.00", filled_at=_ts(hour=15)),
    ]
    out = reconstruct_broker_realized_pnl(fills, TODAY)
    assert out.total == pytest.approx(100.0)
    assert out.order_count == 1  # one closing fill realized PnL
    assert out.non_flat_symbols == []  # flat at day end
    assert out.unbasis_symbols == []


def test_reconstruct_multi_day_swing_realizes_pnl_not_proceeds():
    """The core O8 regression: BUY 2 @ $2.00 on d-3, SELL 2 @ $3.00 on d.

    Realized = (3.00 - 2.00) * 2 * 100 = $200 counted for day d — NOT the
    $600 of same-day sell proceeds the old cashflow approach over-counted.
    """
    fills = [
        _fill(side="buy", qty="2", price="2.00", filled_at=_ts(day=8, hour=15)),
        _fill(side="sell", qty="2", price="3.00", filled_at=_ts(day=11, hour=15)),
    ]
    out = reconstruct_broker_realized_pnl(fills, TODAY)
    assert out.total == pytest.approx(200.0)
    assert out.order_count == 1
    assert out.non_flat_symbols == []


def test_reconstruct_equity_uses_no_multiplier():
    fills = [
        _fill(side="buy", qty="10", price="100.00", ticker="AAPL", filled_at=_ts(hour=14)),
        _fill(side="sell", qty="10", price="101.00", ticker="AAPL", filled_at=_ts(hour=15)),
    ]
    out = reconstruct_broker_realized_pnl(fills, TODAY)
    assert out.total == pytest.approx(10.0)  # 10 * $1, no ×100


def test_reconstruct_partial_close_fifo_lot_accounting():
    """BUY 1 @ $2.00, BUY 1 @ $4.00 on d-1, SELL 1 @ $5.00 on d.

    FIFO consumes the OLDEST lot ($2.00) first: (5.00 - 2.00) * 1 * 100 = $300.
    Remaining open lot ($4.00) leaves the symbol non-flat.
    """
    fills = [
        _fill(side="buy", qty="1", price="2.00", filled_at=_ts(day=10, hour=14)),
        _fill(side="buy", qty="1", price="4.00", filled_at=_ts(day=10, hour=15)),
        _fill(side="sell", qty="1", price="5.00", filled_at=_ts(day=11, hour=15)),
    ]
    out = reconstruct_broker_realized_pnl(fills, TODAY)
    assert out.total == pytest.approx(300.0)
    assert out.order_count == 1
    assert out.non_flat_symbols == [SYM]  # the $4.00 lot still open


def test_reconstruct_close_with_entry_in_lookback_is_counted():
    """A close on d whose entry is within the loaded window is cost-based fine."""
    fills = [
        _fill(side="buy", qty="1", price="2.00", filled_at=_ts(day=5, hour=14)),
        _fill(side="sell", qty="1", price="6.00", filled_at=_ts(day=11, hour=15)),
    ]
    out = reconstruct_broker_realized_pnl(fills, TODAY)
    assert out.total == pytest.approx(400.0)
    assert out.unbasis_symbols == []


def test_reconstruct_close_without_entry_is_unbasis_flagged():
    """A close on d with NO prior open lot (entry predates the loaded window or
    fills missing) → flagged unbasis-able, NOT silently counted."""
    fills = [
        _fill(side="sell", qty="1", price="6.00", filled_at=_ts(day=11, hour=15)),
    ]
    out = reconstruct_broker_realized_pnl(fills, TODAY)
    # No costable open lot → the day-d close is unbasis-able.
    assert out.unbasis_symbols == [SYM]
    assert out.total == 0.0  # nothing trustworthy realized
    assert out.order_count == 0


def test_reconstruct_other_days_realization_does_not_leak_into_day_d():
    """A round trip that CLOSES on d-1 must not contribute to day-d's total."""
    fills = [
        _fill(side="buy", qty="1", price="2.00", filled_at=_ts(day=9, hour=14)),
        _fill(side="sell", qty="1", price="9.00", filled_at=_ts(day=10, hour=15)),
    ]
    out = reconstruct_broker_realized_pnl(fills, TODAY)
    assert out.total == 0.0  # close realized on d-1, not d
    assert out.order_count == 0
    assert out.unbasis_symbols == []  # the d-1 close WAS cost-based, just not day d


def test_reconstruct_unfilled_rows_ignored():
    fills = [
        _fill(side="buy", qty="0", price=None, filled_at=_ts(hour=14)),
        _fill(side="sell", qty="0", price=None, filled_at=_ts(hour=15)),
    ]
    out = reconstruct_broker_realized_pnl(fills, TODAY)
    assert out.total == 0.0
    assert out.order_count == 0


def test_reconstruct_short_then_cover_handles_both_directions():
    """A SELL with no long lots opens a short lot; a later BUY covers it,
    realizing (entry - cover) — the general two-direction path."""
    fills = [
        _fill(side="sell", qty="1", price="5.00", filled_at=_ts(day=10, hour=14)),
        _fill(side="buy", qty="1", price="3.00", filled_at=_ts(day=11, hour=15)),
    ]
    out = reconstruct_broker_realized_pnl(fills, TODAY)
    # short @5 covered @3 → (5 - 3) * 1 * 100 = +$200 on the cover day (d).
    assert out.total == pytest.approx(200.0)
    assert out.order_count == 1
    assert out.non_flat_symbols == []


def test_reconstruct_old_short_cover_overflow_is_unbasis():
    """Round-4 symmetry fix: a day-d BUY that covers MORE than the open short
    lots (the short entry was never recorded) is unbasis-able in the SAME way an
    over-sell is. The old guard only flagged SELLs, so this BUY-side overflow
    slipped through as a trusted-but-wrong total."""
    fills = [
        # one short lot opened earlier...
        _fill(side="sell", qty="1", price="5.00", filled_at=_ts(day=9, hour=14)),
        # ...covered on d by a BUY of 2 — one more than the book carries.
        _fill(side="buy", qty="2", price="3.00", filled_at=_ts(day=11, hour=15)),
    ]
    out = reconstruct_broker_realized_pnl(fills, TODAY)
    assert out.unbasis_symbols == [SYM], "over-cover by a BUY must flag unbasis"


def test_reconstruct_fresh_long_open_not_unbasis():
    """A day-d BUY that opens a fresh long with no opposing lots is a NORMAL entry
    — it must NOT be flagged unbasis (would route every new-position day to
    BROKER_UNAVAILABLE)."""
    fills = [
        _fill(side="buy", qty="3", price="2.00", filled_at=_ts(day=11, hour=15)),
    ]
    out = reconstruct_broker_realized_pnl(fills, TODAY)
    assert out.unbasis_symbols == []
    assert out.total == 0.0  # nothing closed
    assert out.non_flat_symbols == [SYM]  # the new long is open


def test_reconstruct_oversell_after_partial_longs_is_unbasis():
    """A day-d SELL that closes the one open long then keeps going (the rest of
    the basis was never recorded) flags unbasis even though it DID close a lot —
    ``closed_any`` catches the overflow, not just the naked-sell case."""
    fills = [
        _fill(side="buy", qty="1", price="2.00", filled_at=_ts(day=9, hour=14)),
        _fill(side="sell", qty="2", price="6.00", filled_at=_ts(day=11, hour=15)),
    ]
    out = reconstruct_broker_realized_pnl(fills, TODAY)
    assert out.unbasis_symbols == [SYM]


def test_reconstruct_far_old_entry_consumed_oldest_first():
    """Round-4 finding-1: with the full fills history loaded (no finite floor), a
    far-old entry is still in the book, so a day-d SELL costs against the TRUE
    oldest lot rather than a newer one — a correct trusted total, not unbasis."""
    fills = [
        # entry 200 days before d — would have been dropped by a 90d floor.
        _fill(side="buy", qty="1", price="1.00", filled_at=datetime(2025, 11, 23, 14, 0, tzinfo=UTC)),
        # a newer same-symbol entry inside any short window.
        _fill(side="buy", qty="1", price="4.00", filled_at=_ts(day=10, hour=14)),
        # day-d sell of 1 must consume the $1.00 (oldest) lot.
        _fill(side="sell", qty="1", price="5.00", filled_at=_ts(day=11, hour=15)),
    ]
    out = reconstruct_broker_realized_pnl(fills, TODAY)
    assert out.unbasis_symbols == []
    assert out.total == pytest.approx(400.0)  # (5.00 - 1.00) * 1 * 100, oldest lot
    assert out.non_flat_symbols == [SYM]  # the $4.00 lot still open


# ───────────────────────── classification ─────────────────────────


def test_classify_match_within_tolerance():
    journal = JournalTotals(total=100.005, trade_count=1, win_count=1)
    broker = BrokerReconstruction(total=100.0, order_count=1, per_symbol={})
    status, drift_abs, _ = _classify(journal, broker, tolerance_usd=0.01)
    assert status == "MATCH"
    assert drift_abs == pytest.approx(0.005, abs=1e-6)


def test_classify_mismatch_beyond_tolerance():
    journal = JournalTotals(total=150.0, trade_count=1, win_count=1)
    broker = BrokerReconstruction(total=100.0, order_count=1, per_symbol={})
    status, drift_abs, drift_pct = _classify(journal, broker, tolerance_usd=0.01)
    assert status == "MISMATCH"
    assert drift_abs == pytest.approx(50.0)
    assert drift_pct == pytest.approx(50.0)


def test_classify_no_data_when_both_empty():
    journal = JournalTotals(total=0.0, trade_count=0, win_count=0)
    broker = BrokerReconstruction(total=0.0, order_count=0, per_symbol={})
    status, drift_abs, drift_pct = _classify(journal, broker, tolerance_usd=0.01)
    assert status == "NO_DATA"
    assert drift_abs == 0.0
    assert drift_pct is None


def test_tolerance_scales_with_trade_count():
    # 5 trades × $0.01 = $0.05 allowance; drift of $0.04 still matches.
    journal = JournalTotals(total=100.04, trade_count=5, win_count=3)
    broker = BrokerReconstruction(total=100.0, order_count=5, per_symbol={})
    status, _, _ = _classify(journal, broker, tolerance_usd=0.01)
    assert status == "MATCH"


# ───────────────────────── rollups ─────────────────────────


def test_rollup_groups_and_computes_expectancy():
    rows = [
        {"solver_id": "s1", "rule_id": "r1", "realized_pnl": 10.0},
        {"solver_id": "s1", "rule_id": "r1", "realized_pnl": -4.0},
        {"solver_id": "s2", "rule_id": "r2", "realized_pnl": -20.0},
    ]
    by_solver = _rollup(rows, "solver_id")
    by_solver_map = {r.key: r for r in by_solver}
    assert by_solver_map["s1"].n_trades == 2
    assert by_solver_map["s1"].realized_pnl_total == pytest.approx(6.0)
    assert by_solver_map["s1"].expectancy == pytest.approx(3.0)
    assert by_solver_map["s1"].win_count == 1
    assert by_solver_map["s2"].realized_pnl_total == pytest.approx(-20.0)
    # sorted worst-first
    assert by_solver[0].key == "s2"


def test_attribution_row_expectancy_none_when_no_trades():
    row = AttributionRow(key="x", n_trades=0, realized_pnl_total=0.0, win_count=0)
    assert row.expectancy is None


# ───────────────────────── DB-backed journal load + persist ─────────────────────────


async def _seed_journal(
    *,
    solver_id,
    rule_id,
    realized_pnl,
    client_order_id,
    candidate_id,
    filled_at=datetime(2026, 6, 11, 15, tzinfo=UTC),
    exit_filled_at=None,
):
    async def write(session):
        session.add(
            CandidateTrade(
                candidate_id=candidate_id,
                ticker="AAPL",
                timestamp_utc=datetime(2026, 6, 11, 14, tzinfo=UTC),
                rule_id=rule_id,
                direction="LONG",
                confidence=1.0,
                evidence={},
            )
        )
        session.add(
            TradeJournalEntry(
                decision_id=f"dec_{candidate_id}",
                signal_id=f"sig_{candidate_id}",
                candidate_id=candidate_id,
                solver_id=solver_id,
                ticker="AAPL",
                direction="LONG",
                client_order_id=client_order_id,
                broker_order_id="b1",
                realized_pnl=realized_pnl,
                filled_at_utc=filled_at,
                exit_filled_at_utc=exit_filled_at,
                evidence={},
                decision_trace_json={},
                raw_json={},
            )
        )

    await db_write(write)


async def _seed_fill(
    *,
    fill_id: str,
    side: str,
    qty: float,
    price: float,
    ticker: str = SYM,
    client_order_id: str = "orion_x",
    filled_at: datetime,
):
    async def write(session):
        session.add(
            FillRecord(
                id=fill_id,
                ticker=ticker,
                broker_order_id=f"bo_{fill_id}",
                client_order_id=client_order_id,
                filled_qty=qty,
                filled_avg_price=price,
                side=side,
                filled_at_utc=filled_at,
                raw_json={},
            )
        )

    await db_write(write)


async def test_journal_load_attributes_rule_via_candidate_join():
    """rule_id reaches the rollup via journal.candidate_id → candidate.rule_id."""
    await _seed_journal(
        solver_id="solver_a",
        rule_id="BullishSweep",
        realized_pnl=25.0,
        client_order_id="orion_1",
        candidate_id="cand_1",
    )
    rows = await reconcile_pnl._load_journal_closed_trades(TODAY)
    assert len(rows) == 1
    assert rows[0]["solver_id"] == "solver_a"
    assert rows[0]["rule_id"] == "BullishSweep"
    assert rows[0]["realized_pnl"] == pytest.approx(25.0)


async def test_journal_load_excludes_non_orion_and_open():
    # Non-orion attribution.
    await _seed_journal(
        solver_id="s",
        rule_id="r",
        realized_pnl=99.0,
        client_order_id="cerberus_x",
        candidate_id="cand_other",
    )

    # Orion but open (realized_pnl NULL) — seed manually.
    async def write(session):
        session.add(
            TradeJournalEntry(
                decision_id="dec_open",
                signal_id="sig_open",
                candidate_id="cand_open",
                solver_id="s",
                ticker="AAPL",
                direction="LONG",
                client_order_id="orion_open",
                broker_order_id="b",
                realized_pnl=None,
                evidence={},
                decision_trace_json={},
                raw_json={},
            )
        )

    await db_write(write)
    rows = await reconcile_pnl._load_journal_closed_trades(TODAY)
    assert rows == []


async def test_load_orion_fills_filters_attribution_and_window():
    """The fills loader returns only orion fills at-or-before end-of-day d, with
    no lower bound (full history is loaded so FIFO seeds against the true oldest
    lot)."""
    # orion, before end-of-day d
    await _seed_fill(fill_id="f1", side="buy", qty=1, price=2.0, filled_at=_ts(day=8, hour=14))
    await _seed_fill(fill_id="f2", side="sell", qty=1, price=3.0, filled_at=_ts(day=11, hour=15))
    # non-orion — excluded
    await _seed_fill(
        fill_id="f3", side="sell", qty=1, price=9.0, client_order_id="cerberus_y", filled_at=_ts(day=11, hour=15)
    )
    # after end-of-day d — excluded
    await _seed_fill(fill_id="f4", side="sell", qty=1, price=9.0, filled_at=_ts(day=12, hour=1))

    fills = await reconcile_pnl._load_orion_fills(TODAY)
    ids = {f["client_order_id"] for f in fills}
    assert ids == {"orion_x"}
    assert len(fills) == 2  # f1 + f2 only


async def test_persist_reconciliation_writes_all_three_tables():
    result = await compute_reconciliation(TODAY)
    # inject a couple of attribution rows so the rollup tables get data
    result.solver_rows = [AttributionRow("solver_a", 21, -5.0, 8)]
    result.rule_rows = [AttributionRow("BullishSweep", 21, -5.0, 8)]
    await persist_reconciliation(result)

    async def q(session):
        recon = (await session.execute(_select(PnlReconciliation))).scalars().all()
        solvers = (await session.execute(_select(SolverPnlAttribution))).scalars().all()
        rules = (await session.execute(_select(RulePnlAttribution))).scalars().all()
        return recon, solvers, rules

    recon, solvers, rules = await db_query(q)
    assert len(recon) == 1
    assert recon[0].trade_date == TODAY
    assert len(solvers) == 1 and solvers[0].solver_id == "solver_a"
    assert len(rules) == 1 and rules[0].rule_id == "BullishSweep"


async def test_rerun_as_broker_unavailable_clears_prior_attribution():
    """O1b: a covered run writes attribution; a later same-date rerun that flips
    to BROKER_UNAVAILABLE (empty rows) must DELETE the stale attribution, not
    leave it behind as demotion evidence for an untrusted day."""
    # First: a covered run with attribution rows.
    covered = await compute_reconciliation(TODAY)
    covered.solver_rows = [AttributionRow("solver_a", 21, -5.0, 8)]
    covered.rule_rows = [AttributionRow("BullishSweep", 21, -5.0, 8)]
    await persist_reconciliation(covered)

    # Then: a rerun of the SAME date that is broker-unavailable (empty rows).
    untrusted = _broker_unavailable_result(TODAY, covered.journal, covered.tolerance_usd, "fills read failed")
    await persist_reconciliation(untrusted)

    async def q(session):
        solvers = (await session.execute(_select(SolverPnlAttribution))).scalars().all()
        rules = (await session.execute(_select(RulePnlAttribution))).scalars().all()
        recon = (await session.execute(_select(PnlReconciliation))).scalars().all()
        return solvers, rules, recon

    solvers, rules, recon = await db_query(q)
    assert solvers == [] and rules == [], "stale attribution survived a BROKER_UNAVAILABLE rerun"
    assert len(recon) == 1 and recon[0].status == "BROKER_UNAVAILABLE"


async def test_run_reconciliation_end_to_end_match():
    """Journal +$100 vs fills lot-book +$100 round-trip → MATCH, persisted."""
    await _seed_journal(
        solver_id="solver_a",
        rule_id="BullishSweep",
        realized_pnl=100.0,
        client_order_id="orion_1",
        candidate_id="cand_1",
    )
    await _seed_fill(fill_id="b", side="buy", qty=1, price=2.0, filled_at=_ts(hour=14))
    await _seed_fill(fill_id="s", side="sell", qty=1, price=3.0, filled_at=_ts(hour=15))

    result = await run_reconciliation(TODAY)
    assert result.status == "MATCH"
    assert result.journal.total == pytest.approx(100.0)
    assert result.broker.total == pytest.approx(100.0)


async def test_run_reconciliation_multi_day_swing_matches_realized_not_proceeds():
    """End-to-end O8 fix: an entry days ago + close today reconciles on the
    realized delta ($200), not the same-day proceeds ($600)."""
    await _seed_journal(
        solver_id="solver_a",
        rule_id="SwingEntry",
        realized_pnl=200.0,
        client_order_id="orion_1",
        candidate_id="cand_1",
    )
    await _seed_fill(fill_id="b", side="buy", qty=2, price=2.0, filled_at=_ts(day=8, hour=15))
    await _seed_fill(fill_id="s", side="sell", qty=2, price=3.0, filled_at=_ts(day=11, hour=15))

    result = await run_reconciliation(TODAY)
    assert result.status == "MATCH"
    assert result.broker.total == pytest.approx(200.0)


async def test_journal_multi_day_close_buckets_by_exit_day_not_entry_day():
    """Round-6: since the B3 entry-preservation fix, ``filled_at_utc`` keeps the
    ENTRY fill time on a closed row and the close lands in ``exit_filled_at_utc``.
    The journal side must bucket by the EXIT day (matching the lot-book's
    realization-day rule) — bucketing by entry day puts a multi-day hold's PnL
    on the wrong day and forces a false MISMATCH on both days."""
    await _seed_journal(
        solver_id="solver_a",
        rule_id="SwingEntry",
        realized_pnl=200.0,
        client_order_id="orion_1",
        candidate_id="cand_1",
        filled_at=_ts(day=9, hour=15),  # ENTRY fill: day 9
        exit_filled_at=_ts(day=11, hour=15),  # CLOSE fill: day 11 (= TODAY)
    )
    await _seed_fill(fill_id="b", side="buy", qty=2, price=2.0, filled_at=_ts(day=9, hour=15))
    await _seed_fill(fill_id="s", side="sell", qty=2, price=3.0, filled_at=_ts(day=11, hour=15))

    # The close day carries the PnL on BOTH sides → MATCH.
    result = await run_reconciliation(TODAY)
    assert result.status == "MATCH"
    assert result.journal.total == pytest.approx(200.0)
    assert result.broker.total == pytest.approx(200.0)

    # And the ENTRY day must NOT claim the journal PnL.
    entry_day_rows = await reconcile_pnl._load_journal_closed_trades(date(2026, 6, 9))
    assert entry_day_rows == []


async def test_consecutive_mismatch_streak():
    """A prior persisted MISMATCH row makes today the 2nd consecutive day."""

    async def seed_yesterday(session):
        session.add(
            PnlReconciliation(
                trade_date=TODAY - timedelta(days=1),
                journal_total=10.0,
                broker_total=0.0,
                drift_abs=10.0,
                drift_pct=None,
                journal_trade_count=1,
                broker_trade_count=0,
                tolerance_usd=0.01,
                status="MISMATCH",
                details={},
            )
        )

    await db_write(seed_yesterday)
    streak = await reconcile_pnl._count_consecutive_mismatch_days(TODAY, "MISMATCH")
    assert streak == 2


# ─────────── O1: fills read failure → BROKER_UNAVAILABLE ───────────


async def test_fills_read_failure_is_broker_unavailable_not_empty(monkeypatch):
    """O1: a fills-table READ exception must NOT become a valid empty result.

    A DB outage must be distinguishable from a real empty broker day: status
    BROKER_UNAVAILABLE, attribution suppressed, error in details.
    """
    # Journal HAS orion trades — so an empty broker would otherwise look like a
    # confident MISMATCH against $0. The outage must override that.
    await _seed_journal(
        solver_id="solver_a",
        rule_id="BullishSweep",
        realized_pnl=100.0,
        client_order_id="orion_1",
        candidate_id="cand_1",
    )

    async def boom(*_a, **_k):
        raise RuntimeError("timescaledb connection refused")

    monkeypatch.setattr(reconcile_pnl, "_load_orion_fills", boom)
    result = await compute_reconciliation(TODAY)

    assert result.status == STATUS_BROKER_UNAVAILABLE
    assert result.broker_trusted is False
    # attribution + demotion candidates suppressed
    assert result.solver_rows == []
    assert result.rule_rows == []
    # error carried in details
    assert "timescaledb connection refused" in result.details()["broker_unavailable_reason"]
    assert result.details()["broker_trusted"] is False


async def test_fills_read_failure_fires_alert(monkeypatch):
    """O1: BROKER_UNAVAILABLE fires a deduped Discord alert via run_reconciliation."""
    sent: list[tuple[str, str | None]] = []

    async def fake_alert(message, *, dedupe_key=None):
        sent.append((message, dedupe_key))
        return True

    async def boom(*_a, **_k):
        raise RuntimeError("outage")

    monkeypatch.setattr("orion.shared.alerts.send_discord_alert", fake_alert)
    monkeypatch.setattr(reconcile_pnl, "_load_orion_fills", boom)

    result = await run_reconciliation(TODAY)
    assert result.status == STATUS_BROKER_UNAVAILABLE
    assert len(sent) == 1
    msg, dedupe = sent[0]
    assert dedupe == "pnl_reconcile_broker_unavailable"
    assert "BROKER_UNAVAILABLE" in msg


async def test_match_does_not_fire_broker_unavailable_alert(monkeypatch):
    """A normal (covered) reconciliation must NOT fire the unavailable alert."""
    sent: list[str] = []

    async def fake_alert(message, *, dedupe_key=None):
        sent.append(message)
        return True

    monkeypatch.setattr("orion.shared.alerts.send_discord_alert", fake_alert)
    result = await run_reconciliation(TODAY)
    assert result.status != STATUS_BROKER_UNAVAILABLE
    assert sent == []


# ─────────── O8: unbasis-able close → BROKER_UNAVAILABLE ───────────


async def test_unbasis_able_close_routes_to_broker_unavailable():
    """A close on d whose entry predates the loaded window (no open lot) is
    unbasis-able → the whole day is untrusted (no partial reconstruction)."""
    await _seed_journal(
        solver_id="solver_a",
        rule_id="BullishSweep",
        realized_pnl=100.0,
        client_order_id="orion_1",
        candidate_id="cand_1",
    )
    # Only a SELL on day d, no prior BUY anywhere in the window.
    await _seed_fill(fill_id="s", side="sell", qty=1, price=6.0, filled_at=_ts(day=11, hour=15))

    result = await compute_reconciliation(TODAY)
    assert result.status == STATUS_BROKER_UNAVAILABLE
    assert result.broker_trusted is False
    assert result.solver_rows == []
    assert SYM in result.details()["broker_unavailable_reason"]


# ─────────── round-5: pending partial fills → BROKER_UNAVAILABLE ───────────


async def _seed_order(*, order_id: str, status: str, client_order_id: str = "orion_po", ticker: str = SYM):
    from orion.storage.models_execution import OrderRecord

    async def write(session):
        session.add(
            OrderRecord(
                id=order_id,
                ticker=ticker,
                side="sell",
                qty=1,
                client_order_id=client_order_id,
                broker_order_id=f"bo_{order_id}",
                status=status,
                raw_json={},
            )
        )

    await db_write(write)


async def test_pending_partial_fill_routes_to_broker_unavailable():
    """Round-5: FillRecord is ONE cumulative row per order, so a still-
    partially-filled orion order means today's row will MORPH when the order
    completes (e.g. a GTC exit finishing tomorrow) — the day's split is not
    final and must not be trusted, even though basis exists and the unbasis
    guard would never fire."""
    # A normal completed round trip that would otherwise reconcile fine.
    await _seed_fill(fill_id="b", side="buy", qty=1, price=2.0, filled_at=_ts(day=10, hour=14))
    await _seed_fill(fill_id="s", side="sell", qty=1, price=3.0, filled_at=_ts(day=11, hour=15))
    # An orion order still partially filled at reconcile time.
    await _seed_order(order_id="o_partial", status="partially_filled")

    result = await compute_reconciliation(TODAY)
    assert result.status == STATUS_BROKER_UNAVAILABLE
    assert result.solver_rows == []
    assert "partially-filled" in result.details()["broker_unavailable_reason"]
    assert SYM in result.details()["broker_unavailable_reason"]


async def test_non_orion_partial_fill_does_not_block_reconciliation():
    """A sibling system's partially-filled order in the shared account must not
    route orion's day to BROKER_UNAVAILABLE."""
    await _seed_fill(fill_id="b", side="buy", qty=1, price=2.0, filled_at=_ts(day=10, hour=14))
    await _seed_fill(fill_id="s", side="sell", qty=1, price=3.0, filled_at=_ts(day=11, hour=15))
    await _seed_order(order_id="o_sibling", status="partially_filled", client_order_id="cerberus_po")

    result = await compute_reconciliation(TODAY)
    assert result.status != STATUS_BROKER_UNAVAILABLE


async def test_completed_orders_do_not_block_reconciliation():
    """Orders in terminal statuses (filled / canceled-after-partial) have stable
    FillRecord rows — they must not trigger the pending-partial guard."""
    await _seed_fill(fill_id="b", side="buy", qty=1, price=2.0, filled_at=_ts(day=10, hour=14))
    await _seed_fill(fill_id="s", side="sell", qty=1, price=3.0, filled_at=_ts(day=11, hour=15))
    await _seed_order(order_id="o_filled", status="filled")
    await _seed_order(order_id="o_canceled", status="canceled")

    result = await compute_reconciliation(TODAY)
    assert result.status != STATUS_BROKER_UNAVAILABLE


# ───────────────────────── helpers ─────────────────────────


def _select(model):
    from sqlalchemy import select

    return select(model)
