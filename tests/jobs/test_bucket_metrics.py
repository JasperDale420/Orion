"""Nightly bucket-metrics job: closed-trade aggregation and advisory verdicts."""

import os

os.environ["DB_URL"] = "sqlite+aiosqlite:///:memory:"

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from orion.jobs.bucket_halt import RESUMED_STATUS, SET_BY_OPERATOR, active_halts, list_halts, record_halt
from orion.jobs.bucket_metrics import (
    HALT_TRAILING_WINDOW,
    GroupStats,
    compute_bucket_metrics,
    run_bucket_metrics,
)
from orion.storage.db import async_session_factory, init_db
from orion.storage.models_execution import FillRecord
from orion.storage.models_gold import CandidateTrade, ExitDecision
from orion.storage.models_trade_journal import TradeJournalEntry


def _seed_closed_trade(
    session,
    *,
    ticker: str,
    pnl: float,
    dte: int,
    rule_id: str = "rule_swing_v2",
    exit_rule: str | None = "stop_loss_v1",
    notes: str | None = None,
    hold_hours: float = 24.0,
) -> None:
    candidate_id = f"cand_{uuid.uuid4().hex[:10]}"
    entered = datetime.now(UTC) - timedelta(days=2)
    session.add(
        CandidateTrade(
            candidate_id=candidate_id,
            ticker=ticker,
            timestamp_utc=entered,
            rule_id=rule_id,
            direction="LONG",
            expiration_date=entered + timedelta(days=dte),
            evidence={},
        )
    )
    session.add(
        TradeJournalEntry(
            decision_id=f"dec_{uuid.uuid4().hex[:10]}",
            signal_id="sig",
            candidate_id=candidate_id,
            ticker=ticker,
            direction="LONG",
            client_order_id=f"orion_{uuid.uuid4().hex[:8]}",
            broker_order_id=f"broker_{uuid.uuid4().hex[:8]}",
            filled_qty=1.0,
            filled_avg_price=2.0,
            filled_at_utc=entered,
            exit_filled_at_utc=entered + timedelta(hours=hold_hours),
            realized_pnl=pnl,
            notes=notes,
        )
    )
    if exit_rule:
        session.add(
            ExitDecision(
                exit_id=f"exit_{uuid.uuid4().hex[:10]}",
                ticker=ticker,
                candidate_id=candidate_id,
                rule_id=exit_rule,
                exit_reason="test",
                exit_ts_utc=entered + timedelta(hours=hold_hours),
            )
        )


def _seed_measured_exit(
    session,
    *,
    ticker: str,
    dte: int,
    exit_order_id: str,
    mid: float | None,
    mark: float | None,
    fill_price: float | None,
    closed: bool = True,
    with_quote: bool = True,
    link_via_client_order_id: bool = False,
) -> None:
    """Seed one trade whose close carries an exit quote and a matching fill.

    Mirrors production wiring: the journal lot's `raw_json.exit_allocations`
    carries the BROKER order id of the close, `exit_decisions` carries the
    quote, and `fills` carries the realized price (one row per broker order,
    cumulative — partial fills upsert the same row).
    """
    candidate_id = f"cand_{uuid.uuid4().hex[:10]}"
    entered = datetime.now(UTC) - timedelta(days=2)
    exited = entered + timedelta(hours=6)
    client_order_id = f"orion_{uuid.uuid4().hex[:8]}"
    session.add(
        CandidateTrade(
            candidate_id=candidate_id,
            ticker=ticker,
            timestamp_utc=entered,
            rule_id="rule_swing_v2",
            direction="LONG",
            expiration_date=entered + timedelta(days=dte),
            evidence={},
        )
    )
    session.add(
        TradeJournalEntry(
            decision_id=f"dec_{uuid.uuid4().hex[:10]}",
            signal_id="sig",
            candidate_id=candidate_id,
            ticker=ticker,
            direction="LONG",
            client_order_id=f"orion_{uuid.uuid4().hex[:8]}",
            broker_order_id=f"broker_{uuid.uuid4().hex[:8]}",
            filled_qty=1.0,
            filled_avg_price=2.0,
            filled_at_utc=entered,
            exit_filled_at_utc=exited,
            exit_broker_order_id=exit_order_id,
            realized_pnl=25.0 if closed else None,
            raw_json={"exit_allocations": [{"order_id": exit_order_id, "qty": 1.0, "price": fill_price}]},
        )
    )
    quote = None
    if with_quote:
        bid = None if mid is None else round(mid - 0.06, 4)
        ask = None if mid is None else round(mid + 0.06, 4)
        quote = {
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "spread_pct": None if mid in (None, 0) else round((ask - bid) / mid, 4),
            "mark_used_by_rule": mark,
            "decision_ts": exited.isoformat(),
            "source": "gateway_option_quote" if mid is not None else "tracked_mark",
        }
    session.add(
        ExitDecision(
            exit_id=client_order_id,
            ticker=ticker,
            rule_id="stop_loss_v1",
            exit_reason="test",
            exit_ts_utc=exited,
            # A submit response with no id leaves this NULL — the fill is then
            # matched through exit_id == fills.client_order_id.
            broker_order_id=None if link_via_client_order_id else exit_order_id,
            details={"bucket": "SWING", **({"exit_quote": quote} if quote else {})},
        )
    )
    if fill_price is not None:
        session.add(
            FillRecord(
                id=str(uuid.uuid4()),
                ticker=ticker,
                broker_order_id=exit_order_id,
                client_order_id=client_order_id,
                filled_qty=1.0,
                filled_avg_price=fill_price,
                side="sell",
                filled_at_utc=exited,
            )
        )


@pytest.mark.asyncio
async def test_exit_slippage_aggregated_per_bucket():
    """Realized exit cost, per bucket: median/mean vs the quote mid and vs the
    mark the exit rule actually evaluated."""
    await init_db()
    async with async_session_factory() as session:
        # mid 1.00, sold 0.95 → 0.05 (5.0%); mark 1.10 → 0.15 vs mark
        _seed_measured_exit(session, ticker="AAPL", dte=10, exit_order_id="bx1", mid=1.0, mark=1.10, fill_price=0.95)
        # mid 2.00, sold 1.85 → 0.15 (7.5%); mark 2.00 → 0.15 vs mark
        _seed_measured_exit(session, ticker="MSFT", dte=10, exit_order_id="bx2", mid=2.0, mark=2.00, fill_price=1.85)
        # mid 1.00, sold 0.98 → 0.02 (2.0%); mark 1.00 → 0.02 vs mark
        _seed_measured_exit(session, ticker="NVDA", dte=10, exit_order_id="bx3", mid=1.0, mark=1.00, fill_price=0.98)
        await session.commit()

    slip = (await compute_bucket_metrics(days=30))["by_bucket"]["SWING"]["exit_slippage"]

    assert slip["n"] == 3
    assert slip["median_vs_mid_usd"] == pytest.approx(0.05)
    assert slip["mean_vs_mid_usd"] == pytest.approx(0.0733, abs=1e-4)
    assert slip["median_vs_mid_pct"] == pytest.approx(0.05)
    assert slip["mean_vs_mid_pct"] == pytest.approx(0.0483, abs=1e-4)
    assert slip["median_vs_mark_usd"] == pytest.approx(0.15)
    assert slip["mean_vs_mark_usd"] == pytest.approx(0.1067, abs=1e-4)


@pytest.mark.asyncio
async def test_exit_slippage_requires_both_a_quote_and_a_matching_fill():
    await init_db()
    async with async_session_factory() as session:
        # Measurable.
        _seed_measured_exit(session, ticker="AAPL", dte=10, exit_order_id="by1", mid=1.0, mark=1.0, fill_price=0.95)
        # Quote, but the close never filled.
        _seed_measured_exit(session, ticker="MSFT", dte=10, exit_order_id="by2", mid=1.0, mark=1.0, fill_price=None)
        # Filled, but pre-dates the exit-quote capture.
        _seed_measured_exit(
            session, ticker="NVDA", dte=10, exit_order_id="by3", mid=1.0, mark=1.0, fill_price=0.9, with_quote=False
        )
        # Still open — bucket metrics measure CLOSED trades only.
        _seed_measured_exit(
            session, ticker="AMD", dte=10, exit_order_id="by4", mid=1.0, mark=1.0, fill_price=0.9, closed=False
        )
        await session.commit()

    slip = (await compute_bucket_metrics(days=30))["by_bucket"]["SWING"]["exit_slippage"]

    assert slip["n"] == 1
    assert slip["median_vs_mid_usd"] == pytest.approx(0.05)


def _seed_lot_closed_by(
    session,
    *,
    ticker: str,
    dte: int,
    exit_order_id: str,
    realized: bool,
    exit_at: datetime | None = None,
    with_candidate: bool = True,
) -> None:
    """One journal lot with an exit allocation booked against `exit_order_id`.

    `realized=False` is a PARTIALLY closed lot — the allocator books the leg
    before the lot closes, so it is in the ledger but not in the closed set.
    `with_candidate=False` is a lot the allocator resolved by underlying ticker,
    which has no candidate row and therefore no derivable entry DTE.
    """
    entered = datetime.now(UTC) - timedelta(days=2)
    candidate_id = f"cand_{uuid.uuid4().hex[:10]}"
    if with_candidate:
        session.add(
            CandidateTrade(
                candidate_id=candidate_id,
                ticker=ticker,
                timestamp_utc=entered,
                rule_id="rule_swing_v2",
                direction="LONG",
                expiration_date=entered + timedelta(days=dte),
                evidence={},
            )
        )
    session.add(
        TradeJournalEntry(
            decision_id=f"dec_{uuid.uuid4().hex[:10]}",
            signal_id="sig",
            candidate_id=candidate_id if with_candidate else None,
            ticker=ticker,
            direction="LONG",
            filled_qty=2.0,
            filled_avg_price=2.0,
            filled_at_utc=entered,
            exit_filled_qty=2.0 if realized else 1.0,
            exit_filled_at_utc=exit_at if exit_at is not None else entered + timedelta(hours=6),
            exit_broker_order_id=exit_order_id,
            realized_pnl=10.0 if realized else None,
            raw_json={"exit_allocations": [{"order_id": exit_order_id, "qty": 1.0, "price": 0.95}]},
        )
    )


def _seed_quoted_exit_with_fill(session, *, ticker: str, exit_order_id: str) -> None:
    exited = datetime.now(UTC) - timedelta(days=2) + timedelta(hours=6)
    session.add(
        ExitDecision(
            exit_id=f"orion_{exit_order_id}",
            ticker=ticker,
            rule_id="stop_loss_v1",
            exit_reason="test",
            exit_ts_utc=exited,
            broker_order_id=exit_order_id,
            details={"exit_quote": {"bid": 0.94, "ask": 1.06, "mid": 1.0, "mark_used_by_rule": 1.0}},
        )
    )
    session.add(
        FillRecord(
            id=str(uuid.uuid4()),
            ticker=ticker,
            broker_order_id=exit_order_id,
            client_order_id=f"orion_{exit_order_id}",
            filled_qty=2.0,
            filled_avg_price=0.95,
            side="sell",
            filled_at_utc=exited,
        )
    )


@pytest.mark.asyncio
async def test_multi_bucket_exclusion_sees_partially_closed_lots_too():
    """One cumulative close can FULLY close a SWING lot while PARTIALLY closing
    a 0DTE lot. The partial lot is absent from the closed set, so reading only
    closed lots would make the order look single-bucket and charge its whole
    realized price to SWING."""
    await init_db()
    async with async_session_factory() as session:
        _seed_lot_closed_by(session, ticker="AAPL", dte=10, exit_order_id="mixed_order", realized=True)
        _seed_lot_closed_by(session, ticker="AAPL", dte=0, exit_order_id="mixed_order", realized=False)
        _seed_quoted_exit_with_fill(session, ticker="AAPL", exit_order_id="mixed_order")
        await session.commit()

    metrics = await compute_bucket_metrics(days=30)

    assert metrics["by_bucket"]["SWING"]["exit_slippage"]["n"] == 0
    assert metrics["exit_cost_coverage"]["excluded_multi_bucket"] == 1
    assert metrics["exit_cost_coverage"]["measured"] == 0


@pytest.mark.asyncio
async def test_an_out_of_window_repaired_leg_still_counts_toward_multi_bucket_exclusion():
    """Both journal timestamps are the LAST-applied leg's, and the EOD reconcile
    can book an older missed fill after a newer one. A row-level time filter
    would drop that lot and collapse a two-bucket order back to one."""
    await init_db()
    async with async_session_factory() as session:
        _seed_lot_closed_by(session, ticker="AAPL", dte=10, exit_order_id="repaired_order", realized=True)
        _seed_lot_closed_by(
            session,
            ticker="AAPL",
            dte=0,
            exit_order_id="repaired_order",
            realized=False,
            # Its latest leg is an old repaired fill, far outside the window.
            exit_at=datetime.now(UTC) - timedelta(days=400),
        )
        _seed_quoted_exit_with_fill(session, ticker="AAPL", exit_order_id="repaired_order")
        await session.commit()

    coverage = (await compute_bucket_metrics(days=30))["exit_cost_coverage"]

    assert coverage["excluded_multi_bucket"] == 1
    assert coverage["measured"] == 0


@pytest.mark.asyncio
async def test_an_option_lot_is_in_scope_by_its_contract_not_only_its_underlying():
    """An option journal lot carries the UNDERLYING in `ticker` and the OCC
    contract on its candidate, and the allocator matches on either. Scoping the
    ledger read by contract must mirror that, or the lot drops out of the bucket
    map and its close is measured as if it were single-bucket."""
    await init_db()
    entered = datetime.now(UTC) - timedelta(days=2)
    contract = "AAPL260918C00200000"
    async with async_session_factory() as session:
        for dte in (10, 0):
            candidate_id = f"cand_{uuid.uuid4().hex[:10]}"
            session.add(
                CandidateTrade(
                    candidate_id=candidate_id,
                    ticker="AAPL",
                    timestamp_utc=entered,
                    rule_id="rule_swing_v2",
                    direction="LONG",
                    expiration_date=entered + timedelta(days=dte),
                    option_symbol=contract,
                    evidence={},
                )
            )
            session.add(
                TradeJournalEntry(
                    decision_id=f"dec_{uuid.uuid4().hex[:10]}",
                    signal_id="sig",
                    candidate_id=candidate_id,
                    ticker="AAPL",  # the UNDERLYING, not the contract
                    direction="LONG",
                    filled_qty=2.0,
                    filled_avg_price=2.0,
                    filled_at_utc=entered,
                    exit_filled_at_utc=entered + timedelta(hours=6),
                    exit_broker_order_id="occ_order",
                    realized_pnl=10.0 if dte == 10 else None,
                    raw_json={"exit_allocations": [{"order_id": "occ_order", "qty": 1.0, "price": 0.95}]},
                )
            )
        # The close is recorded against the UNDERLYING. Resolving the lots by
        # `coalesce(option_symbol, ticker)` would yield the OCC symbol here and
        # miss them entirely; the allocator's OR matches them on `ticker`.
        _seed_quoted_exit_with_fill(session, ticker="AAPL", exit_order_id="occ_order")
        await session.commit()

    coverage = (await compute_bucket_metrics(days=30))["exit_cost_coverage"]

    assert coverage["excluded_multi_bucket"] == 1
    assert coverage["filled_without_journal_bridge"] == 0, "the lots must be found, not read as an unbridged fill"
    assert coverage["measured"] == 0


@pytest.mark.asyncio
async def test_an_unrelated_in_window_fill_does_not_widen_the_ledger_read():
    """The ledger scan is scoped to contracts that CLAIM a capture. An unrelated
    equity fill on the same underlying must not pull every historical option lot
    on that underlying into scope — an option lot's journal `ticker` IS the
    underlying, so that would be an unbounded read."""
    await init_db()
    exited = datetime.now(UTC) - timedelta(days=2) + timedelta(hours=6)
    async with async_session_factory() as session:
        _seed_measured_exit(session, ticker="AAPL", dte=10, exit_order_id="bq1", mid=1.0, mark=1.0, fill_price=0.95)
        # A sibling equity fill on the same symbol, with no captured exit quote.
        session.add(
            FillRecord(
                id=str(uuid.uuid4()),
                ticker="AAPL",
                broker_order_id="unrelated_equity_order",
                client_order_id="orion_unrelated",
                filled_qty=100.0,
                filled_avg_price=180.0,
                side="sell",
                filled_at_utc=exited,
            )
        )
        await session.commit()

    metrics = await compute_bucket_metrics(days=30)

    assert metrics["exit_cost_coverage"]["quoted_exits"] == 1, "an unquoted fill is not an exit we claim to measure"
    assert metrics["by_bucket"]["SWING"]["exit_slippage"]["n"] == 1


@pytest.mark.asyncio
async def test_an_unquoted_equity_exit_does_not_widen_the_ledger_read():
    """`persist_exit_decision` always writes a details dict, so "has details" is
    NOT "has a capture". If an ordinary equity exit's ticker reached the scan
    scope it would put the UNDERLYING in the predicate and pull in every
    historical option lot on it — an option lot's journal ticker IS the
    underlying."""
    await init_db()
    entered = datetime.now(UTC) - timedelta(days=2)
    exited = entered + timedelta(hours=6)
    async with async_session_factory() as session:
        # A quoted close whose contract matches no lot: on its own it can only
        # ever be an unbridged fill.
        _seed_quoted_exit_with_fill(session, ticker="ZZZZ260918C00100000", exit_order_id="unmatched_order")
        # An ordinary AAPL equity exit — details, but no capture.
        session.add(
            ExitDecision(
                exit_id="orion_equity_exit",
                ticker="AAPL",
                rule_id="stop_loss_v1",
                exit_reason="test",
                exit_ts_utc=exited,
                broker_order_id="equity_order",
                details={"bucket": "SWING", "pnl_pct": -5.0},
            )
        )
        # A historical AAPL lot the equity ticker would drag into scope.
        candidate_id = f"cand_{uuid.uuid4().hex[:10]}"
        session.add(
            CandidateTrade(
                candidate_id=candidate_id,
                ticker="AAPL",
                timestamp_utc=entered,
                rule_id="rule_swing_v2",
                direction="LONG",
                expiration_date=entered + timedelta(days=10),
                evidence={},
            )
        )
        session.add(
            TradeJournalEntry(
                decision_id=f"dec_{uuid.uuid4().hex[:10]}",
                signal_id="sig",
                candidate_id=candidate_id,
                ticker="AAPL",
                direction="LONG",
                filled_qty=1.0,
                filled_avg_price=2.0,
                filled_at_utc=entered,
                exit_filled_at_utc=exited,
                exit_broker_order_id="unmatched_order",
                realized_pnl=10.0,
                raw_json={"exit_allocations": [{"order_id": "unmatched_order", "qty": 1.0, "price": 0.95}]},
            )
        )
        await session.commit()

    coverage = (await compute_bucket_metrics(days=30))["exit_cost_coverage"]

    assert coverage["quoted_exits"] == 1, "only the row carrying a capture is an exit we claim to measure"
    assert coverage["filled_without_journal_bridge"] == 1
    assert coverage["measured"] == 0, "the AAPL lot must stay out of scope"


@pytest.mark.asyncio
async def test_a_lot_with_no_candidate_row_is_not_guessed_into_a_bucket():
    """`bucket_for_dte(None)` defaults to SWING. A lot with no candidate row has
    no derivable DTE at all, so accepting that default would charge an unknown
    lot's exit cost to SWING."""
    await init_db()
    async with async_session_factory() as session:
        _seed_lot_closed_by(
            session, ticker="AAPL", dte=10, exit_order_id="orphan_lot_order", realized=True, with_candidate=False
        )
        _seed_quoted_exit_with_fill(session, ticker="AAPL", exit_order_id="orphan_lot_order")
        await session.commit()

    metrics = await compute_bucket_metrics(days=30)

    assert metrics["exit_cost_coverage"]["unknown_bucket"] == 1
    assert metrics["exit_cost_coverage"]["measured"] == 0
    assert "SWING" not in metrics["by_bucket"]


@pytest.mark.parametrize(
    ("stored_quote", "expected_counter"),
    [
        ({"error": "boom"}, "capture_error"),
        (None, "malformed_quote"),
        ("not-a-quote", "malformed_quote"),
        ([1, 2], "malformed_quote"),
        ({"bid": None, "ask": None, "mid": None, "mark_used_by_rule": None}, "unusable_quote"),
    ],
)
@pytest.mark.asyncio
async def test_an_unmeasurable_quote_is_counted_under_its_own_cause(stored_quote, expected_counter):
    """A capture that failed, a malformed value, and a well-formed quote with no
    benchmark are three different problems — only the first two mean the capture
    itself regressed, so they must not share a counter or hide before the
    denominator."""
    await init_db()
    exited = datetime.now(UTC) - timedelta(days=2) + timedelta(hours=6)
    async with async_session_factory() as session:
        _seed_lot_closed_by(session, ticker="AAPL", dte=10, exit_order_id="broken_quote_order", realized=True)
        session.add(
            ExitDecision(
                exit_id="orion_broken_quote_order",
                ticker="AAPL",
                rule_id="stop_loss_v1",
                exit_reason="test",
                exit_ts_utc=exited,
                broker_order_id="broken_quote_order",
                details={"exit_quote": stored_quote},
            )
        )
        session.add(
            FillRecord(
                id=str(uuid.uuid4()),
                ticker="AAPL",
                broker_order_id="broken_quote_order",
                client_order_id="orion_broken_quote_order",
                filled_qty=2.0,
                filled_avg_price=0.95,
                side="sell",
                filled_at_utc=exited,
            )
        )
        await session.commit()

    coverage = (await compute_bucket_metrics(days=30))["exit_cost_coverage"]

    assert coverage["quoted_exits"] == 1, "a claimed capture counts toward the denominator before it is trusted"
    assert coverage[expected_counter] == 1
    assert coverage["measured"] == 0
    assert coverage["quoted_exits"] == coverage["measured"] + sum(
        v for k, v in coverage.items() if k not in ("quoted_exits", "measured")
    )


@pytest.mark.asyncio
async def test_a_fill_with_no_journal_allocation_is_reported_as_a_bridge_failure():
    """A fill can land while its journal allocation fails (the live path defers
    repair to the EOD reconcile). That is a recovery signal, not an unfilled
    exit, so it must not be lumped in with never-filled closes."""
    await init_db()
    async with async_session_factory() as session:
        _seed_closed_trade(session, ticker="AAPL", pnl=100.0, dte=10)
        _seed_quoted_exit_with_fill(session, ticker="AAPL", exit_order_id="orphan_order")
        await session.commit()

    coverage = (await compute_bucket_metrics(days=30))["exit_cost_coverage"]

    assert coverage["filled_without_journal_bridge"] == 1
    assert coverage["no_matching_fill"] == 0
    assert coverage["measured"] == 0


@pytest.mark.asyncio
async def test_a_bridged_fill_whose_lot_is_still_open_waits_rather_than_counting():
    await init_db()
    async with async_session_factory() as session:
        _seed_lot_closed_by(session, ticker="AAPL", dte=10, exit_order_id="partial_order", realized=False)
        _seed_quoted_exit_with_fill(session, ticker="AAPL", exit_order_id="partial_order")
        await session.commit()

    coverage = (await compute_bucket_metrics(days=30))["exit_cost_coverage"]

    assert coverage["filled_lot_still_open"] == 1
    assert coverage["measured"] == 0


@pytest.mark.asyncio
async def test_one_close_order_spanning_two_buckets_is_excluded_not_guessed():
    """FIFO allocation can close lots of the same contract entered on different
    days — different buckets, one realized price. Charging it to one bucket
    would misstate that bucket's cost, so it is excluded and counted."""
    await init_db()
    entered = datetime.now(UTC) - timedelta(days=2)
    async with async_session_factory() as session:
        for dte, ticker in ((10, "AAPL"), (0, "AAPL")):
            candidate_id = f"cand_{uuid.uuid4().hex[:10]}"
            session.add(
                CandidateTrade(
                    candidate_id=candidate_id,
                    ticker=ticker,
                    timestamp_utc=entered,
                    rule_id="rule_swing_v2",
                    direction="LONG",
                    expiration_date=entered + timedelta(days=dte),
                    evidence={},
                )
            )
            session.add(
                TradeJournalEntry(
                    decision_id=f"dec_{uuid.uuid4().hex[:10]}",
                    signal_id="sig",
                    candidate_id=candidate_id,
                    ticker=ticker,
                    direction="LONG",
                    filled_qty=1.0,
                    filled_avg_price=2.0,
                    filled_at_utc=entered,
                    exit_filled_at_utc=entered + timedelta(hours=6),
                    exit_broker_order_id="shared_order",
                    realized_pnl=10.0,
                    raw_json={"exit_allocations": [{"order_id": "shared_order", "qty": 1.0, "price": 0.95}]},
                )
            )
        session.add(
            ExitDecision(
                exit_id="orion_shared",
                ticker="AAPL",
                rule_id="stop_loss_v1",
                exit_reason="test",
                exit_ts_utc=entered + timedelta(hours=6),
                broker_order_id="shared_order",
                details={"exit_quote": {"bid": 0.94, "ask": 1.06, "mid": 1.0, "mark_used_by_rule": 1.0}},
            )
        )
        session.add(
            FillRecord(
                id=str(uuid.uuid4()),
                ticker="AAPL",
                broker_order_id="shared_order",
                client_order_id="orion_shared",
                filled_qty=2.0,
                filled_avg_price=0.95,
                side="sell",
                filled_at_utc=entered + timedelta(hours=6),
            )
        )
        await session.commit()

    metrics = await compute_bucket_metrics(days=30)

    assert metrics["by_bucket"]["SWING"]["exit_slippage"]["n"] == 0
    assert metrics["by_bucket"]["0DTE"]["exit_slippage"]["n"] == 0
    assert metrics["exit_cost_coverage"]["excluded_multi_bucket"] == 1
    assert metrics["exit_cost_coverage"]["measured"] == 0


@pytest.mark.asyncio
async def test_exit_cost_coverage_reports_the_unmeasured_denominator():
    """A native-escalation close carries a quote but no orion-attributed fill.
    It must be COUNTED as unmeasured, not silently dropped — escalations are the
    expensive exits, and a mean that quietly omits them flatters reality."""
    await init_db()
    async with async_session_factory() as session:
        _seed_measured_exit(session, ticker="AAPL", dte=10, exit_order_id="bv1", mid=1.0, mark=1.0, fill_price=0.95)
        _seed_measured_exit(session, ticker="MSFT", dte=10, exit_order_id="bv2", mid=1.0, mark=1.0, fill_price=None)
        await session.commit()

    coverage = (await compute_bucket_metrics(days=30))["exit_cost_coverage"]

    assert coverage == {
        "quoted_exits": 2,
        "measured": 1,
        "no_matching_fill": 1,
        "filled_without_journal_bridge": 0,
        "filled_lot_still_open": 0,
        "excluded_multi_bucket": 0,
        "duplicate_order": 0,
        "unknown_bucket": 0,
        "unusable_quote": 0,
        "malformed_quote": 0,
        "capture_error": 0,
    }
    # The counters account for every quoted exit — no silent extra category.
    assert coverage["quoted_exits"] == coverage["measured"] + sum(
        v for k, v in coverage.items() if k not in ("quoted_exits", "measured")
    )


@pytest.mark.asyncio
async def test_exit_slippage_falls_back_to_the_client_order_id_join():
    """A close whose submit response carried no broker id leaves
    `exit_decisions.broker_order_id` NULL; `exit_id` IS the client_order_id, and
    the fill that lands later carries it too."""
    await init_db()
    async with async_session_factory() as session:
        _seed_measured_exit(
            session,
            ticker="AAPL",
            dte=10,
            exit_order_id="bz1",
            mid=1.0,
            mark=1.0,
            fill_price=0.95,
            link_via_client_order_id=True,
        )
        await session.commit()

    slip = (await compute_bucket_metrics(days=30))["by_bucket"]["SWING"]["exit_slippage"]

    assert slip["n"] == 1
    assert slip["median_vs_mid_usd"] == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_mark_only_exit_quote_still_measures_against_the_mark():
    """The tracked-mark fallback has no bid/ask — vs-mid is unmeasurable, but
    the mark the rule acted on still is."""
    await init_db()
    async with async_session_factory() as session:
        _seed_measured_exit(session, ticker="AAPL", dte=10, exit_order_id="bw1", mid=None, mark=2.0, fill_price=1.8)
        await session.commit()

    slip = (await compute_bucket_metrics(days=30))["by_bucket"]["SWING"]["exit_slippage"]

    assert slip["n"] == 1
    assert slip["median_vs_mid_usd"] is None
    assert slip["median_vs_mark_usd"] == pytest.approx(0.20)


@pytest.mark.asyncio
async def test_buckets_without_measured_exits_report_an_empty_slippage_block():
    await init_db()
    async with async_session_factory() as session:
        _seed_closed_trade(session, ticker="AAPL", pnl=100.0, dte=10)
        await session.commit()

    slip = (await compute_bucket_metrics(days=30))["by_bucket"]["SWING"]["exit_slippage"]

    assert slip["n"] == 0
    assert slip["median_vs_mid_usd"] is None


@pytest.mark.asyncio
async def test_metrics_aggregate_by_bucket_and_rule():
    await init_db()
    async with async_session_factory() as session:
        # Two SWING winners, one SWING loser (dte=10), one 0DTE loser.
        _seed_closed_trade(session, ticker="AAPL", pnl=100.0, dte=10, exit_rule="profit_target_v1")
        _seed_closed_trade(session, ticker="MSFT", pnl=50.0, dte=10, exit_rule="profit_target_v1")
        _seed_closed_trade(session, ticker="NVDA", pnl=-75.0, dte=10, exit_rule="stop_loss_v1")
        _seed_closed_trade(
            session, ticker="SPY", pnl=-40.0, dte=0, rule_id="rule_0dte_sweep_v2", exit_rule="zero_dte_flatten_v1"
        )
        await session.commit()

    metrics = await compute_bucket_metrics(days=30)

    assert metrics["closed_trades"] == 4
    swing = metrics["by_bucket"]["SWING"]
    assert swing["n"] == 3
    assert swing["win_rate"] == pytest.approx(2 / 3, abs=0.01)
    assert swing["total_pnl"] == pytest.approx(75.0)
    assert swing["profit_factor"] == pytest.approx(150 / 75, abs=0.01)
    assert swing["exit_reason_mix"] == {"profit_target_v1": 2, "stop_loss_v1": 1}
    assert swing["verdict"] == "collecting"  # n < 30: touch nothing

    zero = metrics["by_bucket"]["0DTE"]
    assert zero["n"] == 1 and zero["win_rate"] == 0.0

    assert metrics["by_rule"]["rule_swing_v2"]["n"] == 3
    assert metrics["by_rule"]["rule_0dte_sweep_v2"]["n"] == 1


@pytest.mark.asyncio
async def test_window_keys_on_close_time_not_entry_time():
    """A multi-day hold entered BEFORE the window but closed inside it must
    be counted (window on realization time, not entry fill time)."""
    await init_db()
    entered = datetime.now(UTC) - timedelta(days=12)
    async with async_session_factory() as session:
        candidate_id = f"cand_{uuid.uuid4().hex[:10]}"
        session.add(
            CandidateTrade(
                candidate_id=candidate_id,
                ticker="AAPL",
                timestamp_utc=entered,
                rule_id="rule_swing_v2",
                direction="LONG",
                expiration_date=entered + timedelta(days=14),
                evidence={},
            )
        )
        session.add(
            TradeJournalEntry(
                decision_id=f"dec_{uuid.uuid4().hex[:10]}",
                signal_id="sig",
                candidate_id=candidate_id,
                ticker="AAPL",
                direction="LONG",
                client_order_id=f"orion_{uuid.uuid4().hex[:8]}",
                broker_order_id=f"broker_{uuid.uuid4().hex[:8]}",
                filled_qty=1.0,
                filled_avg_price=2.0,
                filled_at_utc=entered,  # 12 days ago — outside a 7-day window
                exit_filled_at_utc=datetime.now(UTC) - timedelta(days=1),  # closed yesterday
                realized_pnl=42.0,
            )
        )
        await session.commit()

    metrics = await compute_bucket_metrics(days=7)
    assert metrics["closed_trades"] == 1
    assert metrics["by_bucket"]["SWING"]["total_pnl"] == pytest.approx(42.0)


@pytest.mark.asyncio
async def test_expired_worthless_counts_with_its_own_exit_reason():
    await init_db()
    async with async_session_factory() as session:
        _seed_closed_trade(session, ticker="IONQ", pnl=-200.0, dte=5, exit_rule=None, notes="expired_worthless")
        await session.commit()

    metrics = await compute_bucket_metrics(days=30)
    swing = metrics["by_bucket"]["SWING"]
    assert swing["exit_reason_mix"] == {"expired_worthless": 1}


def test_verdicts_follow_sample_size_discipline():
    # Sizing up requires n>=100, positive expectancy, PF>=1.15.
    good = GroupStats()
    for i in range(120):
        good.add(60.0 if i % 2 == 0 else -40.0, 5.0, "profit_target_v1")
    assert good.summary()["verdict"] == "consider_sizing_up"

    # A trailing-50 collapse flags halting even with a decent lifetime PF.
    # newest-first list: add() appends, so add the LOSERS last... pnls are
    # appended in insertion order and trailing reads the FIRST 50 — seed the
    # bad run first (it reads as most recent).
    bad_recent = GroupStats()
    for _ in range(HALT_TRAILING_WINDOW):
        bad_recent.add(-50.0, 5.0, "stop_loss_v1")
    for _ in range(200):
        bad_recent.add(80.0, 5.0, "profit_target_v1")
    assert bad_recent.summary()["verdict"] == "consider_halting"

    # Tiny sample: no verdict beyond collecting.
    tiny = GroupStats()
    for _ in range(5):
        tiny.add(100.0, 5.0, "profit_target_v1")
    assert tiny.summary()["verdict"] == "collecting"


@pytest.mark.asyncio
async def test_routine_metrics_are_logged_without_discord_page():
    metrics = {
        "window_days": 30,
        "closed_trades": 5,
        "by_bucket": {
            "SWING": {
                "n": 5,
                "win_rate": 0.4,
                "expectancy": -2.0,
                "profit_factor": 0.9,
                "avg_hold_hours": 12.0,
                "exit_reason_mix": {"stop_loss_v1": 3},
                "verdict": "collecting",
            }
        },
        "by_rule": {},
    }
    sent = AsyncMock(return_value=True)

    with (
        patch("orion.jobs.bucket_metrics.compute_bucket_metrics", AsyncMock(return_value=metrics)),
        patch("orion.shared.alerts.send_discord_alert", sent),
    ):
        assert await run_bucket_metrics() == metrics

    sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_actionable_metrics_still_page_discord():
    metrics = _halting_metrics()
    sent = AsyncMock(return_value=True)

    with (
        patch("orion.jobs.bucket_metrics.compute_bucket_metrics", AsyncMock(return_value=metrics)),
        patch("orion.shared.alerts.send_discord_alert", sent),
    ):
        await run_bucket_metrics()

    sent.assert_awaited_once()


# ── Verdicts that act: the halting verdict writes a durable entry halt ────


def _halting_metrics(n: int = 100, trailing_pf: float = 0.5) -> dict:
    return {
        "window_days": 30,
        "closed_trades": n,
        "by_bucket": {
            "SWING": {
                "n": n,
                "win_rate": 0.2,
                "expectancy": -20.0,
                "profit_factor": 0.5,
                "trailing_pf": trailing_pf,
                "avg_hold_hours": 12.0,
                "exit_reason_mix": {"stop_loss_v1": 80},
                "verdict": "consider_halting",
            }
        },
        "by_rule": {},
    }


@pytest.mark.asyncio
async def test_halting_verdict_writes_a_halt_row_once():
    await init_db()
    metrics = _halting_metrics()

    with (
        patch("orion.jobs.bucket_metrics.compute_bucket_metrics", AsyncMock(return_value=metrics)),
        patch("orion.shared.alerts.send_discord_alert", AsyncMock(return_value=True)),
    ):
        await run_bucket_metrics()
        first = await active_halts()
        assert first["SWING"].profit_factor == 0.5
        assert first["SWING"].n_closed == 100

        # A second nightly pass must neither duplicate nor extend the halt.
        await run_bucket_metrics()

    second = await active_halts()
    assert list(second) == ["SWING"]
    assert second["SWING"].expires_after_session == first["SWING"].expires_after_session


@pytest.mark.asyncio
async def test_collecting_verdict_on_a_tiny_sample_writes_no_halt():
    await init_db()
    metrics = _halting_metrics(n=5)
    metrics["by_bucket"]["SWING"]["verdict"] = "collecting"

    with (
        patch("orion.jobs.bucket_metrics.compute_bucket_metrics", AsyncMock(return_value=metrics)),
        patch("orion.shared.alerts.send_discord_alert", AsyncMock(return_value=True)),
    ):
        await run_bucket_metrics()

    assert await active_halts() == {}


@pytest.mark.asyncio
async def test_halting_verdict_under_the_trailing_window_writes_no_halt():
    """Defence in depth: the criterion needs a full trailing window behind it."""
    await init_db()
    metrics = _halting_metrics(n=HALT_TRAILING_WINDOW - 1)

    with (
        patch("orion.jobs.bucket_metrics.compute_bucket_metrics", AsyncMock(return_value=metrics)),
        patch("orion.shared.alerts.send_discord_alert", AsyncMock(return_value=True)),
    ):
        await run_bucket_metrics()

    assert await active_halts() == {}


@pytest.mark.asyncio
async def test_nightly_pass_releases_an_expired_halt():
    """Time-boxed: a halted bucket has to be able to resume and prove itself."""
    await init_db()
    stale = datetime.now(UTC) - timedelta(days=120)
    await record_halt("POSITION", profit_factor=0.3, n_closed=60, now=stale)

    metrics = _halting_metrics()
    metrics["by_bucket"] = {}

    with (
        patch("orion.jobs.bucket_metrics.compute_bucket_metrics", AsyncMock(return_value=metrics)),
        patch("orion.shared.alerts.send_discord_alert", AsyncMock(return_value=True)),
    ):
        await run_bucket_metrics()

    # The bucket stops gating and starts its sampling window.
    assert await active_halts() == {}
    assert [(h.bucket, h.status) for h in await list_halts()] == [("POSITION", RESUMED_STATUS)]


@pytest.mark.asyncio
async def test_a_released_bucket_is_not_rehalted_by_the_same_nightly_pass():
    """A halted bucket closes no new trades, so the trailing fifty that halted
    it are unchanged the night its window lapses. Re-halting on them would make
    the ten-session time-box a permanent halt in disguise."""
    await init_db()
    stale = datetime.now(UTC) - timedelta(days=120)
    await record_halt("SWING", profit_factor=0.5, n_closed=100, now=stale)

    with (
        patch("orion.jobs.bucket_metrics.compute_bucket_metrics", AsyncMock(return_value=_halting_metrics())),
        patch("orion.shared.alerts.send_discord_alert", AsyncMock(return_value=True)),
    ):
        await run_bucket_metrics()

    assert await active_halts() == {}
    assert [(h.bucket, h.status) for h in await list_halts()] == [("SWING", RESUMED_STATUS)]


@pytest.mark.asyncio
async def test_nightly_pass_never_overwrites_an_operator_halt():
    await init_db()
    await record_halt("SWING", profit_factor=None, n_closed=None, set_by=SET_BY_OPERATOR, reason="operator hold")

    with (
        patch("orion.jobs.bucket_metrics.compute_bucket_metrics", AsyncMock(return_value=_halting_metrics())),
        patch("orion.shared.alerts.send_discord_alert", AsyncMock(return_value=True)),
    ):
        await run_bucket_metrics()

    halts = await active_halts()
    assert halts["SWING"].set_by == SET_BY_OPERATOR
    assert halts["SWING"].reason == "operator hold"


@pytest.mark.asyncio
async def test_halt_write_failure_does_not_lose_the_advisory_alert():
    """The halt is an addition to the advisory path, not a replacement."""
    await init_db()
    sent = AsyncMock(return_value=True)

    with (
        patch("orion.jobs.bucket_metrics.compute_bucket_metrics", AsyncMock(return_value=_halting_metrics())),
        patch("orion.jobs.bucket_metrics.apply_halt_verdicts", AsyncMock(side_effect=RuntimeError("db down"))),
        patch("orion.shared.alerts.send_discord_alert", sent),
    ):
        await run_bucket_metrics()

    sent.assert_awaited_once()
