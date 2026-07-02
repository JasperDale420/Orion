"""Nightly bucket-metrics job: closed-trade aggregation and advisory verdicts."""

import os

os.environ["DB_URL"] = "sqlite+aiosqlite:///:memory:"

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from orion.jobs.bucket_metrics import (
    HALT_TRAILING_WINDOW,
    GroupStats,
    compute_bucket_metrics,
)
from orion.storage.db import async_session_factory, init_db
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
