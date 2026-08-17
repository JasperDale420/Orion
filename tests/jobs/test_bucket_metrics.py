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
