from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from orion.storage.db import async_session_factory
from orion.storage.models import SystemStatus
from orion.storage.models_execution import OrderRecord
from orion.storage.models_gold import CandidateTrade, StrategyDecision

E2E_DIR = Path(__file__).resolve().parent
if str(E2E_DIR) not in sys.path:
    sys.path.append(str(E2E_DIR))

import test_smoke_e2e as smoke_mod


def test_ticker_for_run_is_namespaced() -> None:
    ticker = smoke_mod._ticker_for_run("smoke_1775161339")

    assert ticker != "SPY"
    assert ticker.startswith("SM")
    assert len(ticker) <= 6


@pytest.mark.asyncio
async def test_cleanup_smoke_run_rows_leaves_unrelated_rows_intact() -> None:
    from orion.storage.models import BronzeEvent
    from orion.storage.models_gold import GoldTickerRollup
    from orion.storage.models_silver import SilverSignal
    from orion.storage.models_solvers import Solver, SolverMetrics

    run_tag = "smoke_1775161339"
    smoke_ticker = "SM1339"
    other_ticker = "KEEP"
    keep_candidate_id = "keep_candidate"
    keep_signal_id = "keep_signal"
    now = datetime.now(UTC)

    async with async_session_factory() as session:
        session.add_all(
            [
                BronzeEvent(
                    event_id=f"{run_tag}_bar_1",
                    source="ALPACA",
                    source_event_id="smoke_source",
                    event_type="ALPACA_BAR_1M",
                    ticker=smoke_ticker,
                    trading_date=None,
                    session="REG",
                    schema_version="v1",
                    event_ts_utc=now,
                    received_ts_utc=now,
                    payload={},
                    ingest={},
                ),
                SilverSignal(
                    signal_id=f"{run_tag}_signal",
                    ticker=smoke_ticker,
                    signal_ts_utc=now,
                    signal_type="FEATURE_EVENT",
                    features={},
                    created_at_utc=now,
                ),
                SilverSignal(
                    signal_id=keep_signal_id,
                    ticker=smoke_ticker,
                    signal_ts_utc=now + timedelta(minutes=1),
                    signal_type="FEATURE_EVENT",
                    features={},
                    created_at_utc=now + timedelta(minutes=1),
                ),
                CandidateTrade(
                    candidate_id=f"{run_tag}_candidate",
                    ticker=smoke_ticker,
                    timestamp_utc=now,
                    rule_id="smoke_rule",
                    direction="LONG",
                    confidence=1.0,
                    evidence={},
                ),
                CandidateTrade(
                    candidate_id=keep_candidate_id,
                    ticker=smoke_ticker,
                    timestamp_utc=now + timedelta(minutes=1),
                    rule_id="keep_rule",
                    direction="LONG",
                    confidence=1.0,
                    evidence={},
                ),
                StrategyDecision(
                    decision_id=f"{run_tag}_decision",
                    candidate_id=f"{run_tag}_candidate",
                    ticker=smoke_ticker,
                    strategy_version_id=f"{run_tag}_solver",
                    decision="SKIP",
                    timestamp_utc=now,
                ),
                StrategyDecision(
                    decision_id="keep_decision",
                    candidate_id=keep_candidate_id,
                    ticker=smoke_ticker,
                    strategy_version_id="keep_solver",
                    decision="SKIP",
                    timestamp_utc=now + timedelta(minutes=1),
                ),
                GoldTickerRollup(
                    ticker=smoke_ticker,
                    period="5m",
                    timestamp_utc=now,
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                    volume=1.0,
                    vwap=1.0,
                ),
                GoldTickerRollup(
                    ticker=smoke_ticker,
                    period="1h",
                    timestamp_utc=now + timedelta(minutes=1),
                    open=2.0,
                    high=2.0,
                    low=2.0,
                    close=2.0,
                    volume=2.0,
                    vwap=2.0,
                ),
                Solver(
                    solver_id=f"{run_tag}_solver",
                    family_name="Smoke",
                    config={"version_id": f"{run_tag}_solver"},
                    definition_json={"version_id": f"{run_tag}_solver"},
                    is_active=True,
                    stage="paper",
                    status="active",
                ),
                SolverMetrics(
                    id=f"{run_tag}_metric",
                    solver_id=f"{run_tag}_solver",
                    sector="ALL",
                    dataset_tag="smoke",
                    num_runs=1,
                    num_trades=1,
                    sharpe_ratio=1.0,
                    info_ratio=1.0,
                    profit_factor=1.0,
                    max_dd_pct=1.0,
                    stability_score=1.0,
                    metrics_json={},
                    oos_expect_bp=1.0,
                ),
                OrderRecord(
                    id=f"{run_tag}_order_row",
                    ticker=smoke_ticker,
                    side="buy",
                    qty=1,
                    client_order_id=f"{run_tag}_client_order",
                    broker_order_id=smoke_mod._smoke_broker_order_id(run_tag),
                    status="accepted",
                    raw_json={},
                    system="orion",
                ),
                OrderRecord(
                    id=f"{run_tag}_order_row_current",
                    ticker=smoke_ticker,
                    side="buy",
                    qty=1,
                    client_order_id=f"{run_tag}_client_order_current",
                    broker_order_id=smoke_mod._smoke_order_id(run_tag),
                    status="accepted",
                    raw_json={},
                    system="orion",
                ),
                OrderRecord(
                    id="keep_order_row",
                    ticker=other_ticker,
                    side="buy",
                    qty=1,
                    client_order_id="keep_client_order",
                    broker_order_id="keep_order_001",
                    status="accepted",
                    raw_json={},
                    system="orion",
                ),
            ]
        )
        await session.commit()

        await smoke_mod._cleanup_smoke_run_rows(
            session,
            run_tag=run_tag,
            ticker=smoke_ticker,
            solver_metric_ids=[f"{run_tag}_metric"],
            bronze_event_ids=[f"{run_tag}_bar_1"],
            silver_signal_ids=[f"{run_tag}_signal"],
            candidate_ids=[f"{run_tag}_candidate"],
            rollup_keys=[("5m", now)],
        )
        await session.commit()

    async with async_session_factory() as session:
        keep_order = await session.scalar(select(OrderRecord).where(OrderRecord.broker_order_id == "keep_order_001"))
        smoke_order = await session.scalar(
            select(OrderRecord).where(OrderRecord.broker_order_id == smoke_mod._smoke_order_id(run_tag))
        )
        legacy_smoke_order = await session.scalar(
            select(OrderRecord).where(OrderRecord.broker_order_id == smoke_mod._smoke_broker_order_id(run_tag))
        )
        smoke_candidate = await session.scalar(
            select(CandidateTrade).where(CandidateTrade.candidate_id == f"{run_tag}_candidate")
        )
        keep_candidate = await session.scalar(
            select(CandidateTrade).where(CandidateTrade.candidate_id == keep_candidate_id)
        )
        keep_signal = await session.scalar(select(SilverSignal).where(SilverSignal.signal_id == keep_signal_id))
        keep_rollup = await session.scalar(
            select(GoldTickerRollup).where(
                GoldTickerRollup.ticker == smoke_ticker,
                GoldTickerRollup.period == "1h",
                GoldTickerRollup.timestamp_utc == now + timedelta(minutes=1),
            )
        )
        smoke_solver = await session.scalar(select(Solver).where(Solver.solver_id == f"{run_tag}_solver"))

    assert keep_order is not None
    assert smoke_order is None
    assert legacy_smoke_order is None
    assert smoke_candidate is None
    assert keep_candidate is not None
    assert keep_signal is not None
    assert keep_rollup is not None
    assert smoke_solver is None


@pytest.mark.asyncio
async def test_execute_smoke_order_avoids_live_db_pollution() -> None:
    run_tag = "smoke_1775161339"
    now = datetime.now(UTC)
    decision = StrategyDecision(
        decision_id="smoke_decision",
        candidate_id="smoke_candidate",
        ticker="SM1339",
        strategy_version_id="smoke_solver",
        decision="EXECUTE",
        timestamp_utc=now,
        execution_params={"risk_per_trade_bps": 50, "regime_size_multiplier": 1.0},
    )
    candidate = CandidateTrade(
        candidate_id="smoke_candidate",
        ticker="SM1339",
        timestamp_utc=now,
        rule_id="smoke_rule",
        direction="LONG",
        confidence=1.0,
        option_symbol="SM1339260411C00560000",
        strike_price=560.0,
        expiration_date=now + timedelta(days=10),
        option_type="CALL",
        underlying_price=560.75,
        premium=3.5,
        evidence={},
    )

    async with async_session_factory() as session:
        session.add(
            SystemStatus(
                key="global_health",
                status="HEALTHY",
                details="baseline",
                last_updated_utc=now,
            )
        )
        await session.commit()

    await smoke_mod._execute_mock_smoke_order(run_tag=run_tag, decision=decision, candidate=candidate)

    async with async_session_factory() as session:
        status = await session.get(SystemStatus, "global_health")
        smoke_order = await session.scalar(
            select(OrderRecord).where(OrderRecord.broker_order_id == smoke_mod._smoke_order_id(run_tag))
        )
        legacy_smoke_order = await session.scalar(
            select(OrderRecord).where(OrderRecord.broker_order_id == smoke_mod._smoke_broker_order_id(run_tag))
        )

    assert status is not None
    assert status.details == "baseline"
    assert smoke_order is None
    assert legacy_smoke_order is None
