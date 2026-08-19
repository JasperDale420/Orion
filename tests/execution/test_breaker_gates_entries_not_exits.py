"""An OPEN circuit breaker halts new entries and never blocks an exit.

Two invariants, both safety-critical and easy to break in opposite directions:

  * ENTRIES: every process that can open a position must refuse to while the
    `GLOBAL_CIRCUIT_BREAKER` row is OPEN — including a breaker opened by the
    drawdown kill switch.
  * EXITS: nothing on the risk-reducing path may consult the breaker. A halted
    system must still be able to get flat. The execution loop used to `continue`
    on an open breaker, which skipped the rule-based exit sweep at the bottom of
    the same iteration — a latched breaker would have stranded every position.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import delete

from orion import main_execution
from orion.config import RiskSettings
from orion.core import circuit_breaker as circuit_breaker_module
from orion.core.circuit_breaker import CircuitBreaker
from orion.core.enums import DecisionAction
from orion.execution.execution_engine import ExecutionEngine
from orion.storage.db import async_session_factory, init_db
from orion.storage.models import SystemStatus

pytestmark = pytest.mark.asyncio

OCC = "AAPL260821C00250000"


def _candidate():
    return SimpleNamespace(
        ticker="AAPL",
        option_symbol=OCC,
        direction="LONG",
        timestamp_utc=datetime.now(UTC),
        expiration_date=None,
        execution_params={},
    )


def _decision():
    return SimpleNamespace(
        decision="EXECUTE",
        decision_id="d-1",
        executed_successfully=None,
        execution_log=None,
        reason=None,
        execution_params={},
        decision_trace_json=None,
    )


async def _seed_healthy_system() -> None:
    """A fresh HEALTHY `global_health` row, so the only thing under test is
    the breaker rather than the ingestion-liveness gate next to it."""
    async with async_session_factory() as session:
        await session.merge(
            SystemStatus(
                key="global_health",
                status="HEALTHY",
                details="test",
                last_updated_utc=datetime.now(UTC),
            )
        )
        await session.commit()


def _entry_engine():
    engine = ExecutionEngine()
    engine._health_cache = None
    engine.risk_manager = MagicMock()
    engine.risk_manager.ticker_exposures = {}
    engine.risk_manager.config = RiskSettings()
    return engine


# ── Entries ──────────────────────────────────────────────────────────────


async def test_engine_preflight_blocks_entry_when_breaker_open() -> None:
    """The engine-level gate, shared by every process that holds an engine."""
    await init_db()
    await CircuitBreaker().open("Manual halt")

    engine = _entry_engine()
    decision = _decision()

    assert await engine._pre_flight_checks(decision, _candidate()) is False


async def test_signal_preflight_rejects_entry_when_breaker_open() -> None:
    """The earlier gate in the execution loop, before the engine is reached."""
    from orion.execution.signal_preflight import preflight_live_signal

    await init_db()
    await CircuitBreaker().open("Manual halt")

    result = await preflight_live_signal(
        None,
        candidate=_candidate(),
        decision=_decision(),
        risk_manager=MagicMock(),
    )

    assert result.ok is False
    assert result.reason == "Circuit Breaker Open"


async def test_drawdown_kill_switch_opens_breaker_and_blocks_entries(monkeypatch) -> None:
    """The kill switch must still be able to latch the breaker, and latching it
    must actually stop new entries."""
    from orion.execution.risk.manager import RiskManager

    monkeypatch.setattr("orion.execution.risk.manager._metrics", MagicMock())
    await init_db()
    await CircuitBreaker().close()

    risk = RiskManager(config=RiskSettings(max_daily_loss=1e9, max_drawdown_pct=0.05))
    risk.current_equity = 1000.0
    risk.starting_equity = 1000.0
    risk.peak_equity = 1000.0
    risk.positions["SPY"] = {"qty": 10.0, "avg_entry": 100.0}

    await risk.process_fill("SPY", qty=10.0, price=90.0, side="sell", fill_id="dd-entry-gate")

    assert await CircuitBreaker().is_open() is True
    assert await _entry_engine()._pre_flight_checks(_decision(), _candidate()) is False


# ── Exits ────────────────────────────────────────────────────────────────


async def test_close_position_submits_while_breaker_open(monkeypatch) -> None:
    """Getting flat is always allowed. An open breaker must not reach here."""
    await init_db()
    await CircuitBreaker().open("Drawdown kill switch")

    engine = ExecutionEngine()
    engine._check_gateway_available = AsyncMock(return_value=True)
    schedule = MagicMock()
    schedule.is_market_open_for_options.return_value = True
    engine._market_schedule = schedule

    client = AsyncMock()
    client.get_position = AsyncMock(return_value={"symbol": OCC, "qty": "10", "avg_entry_price": "1.0"})
    client.create_order = AsyncMock(return_value={"id": "o1", "status": "accepted"})
    client.get_orders = AsyncMock(return_value=[])
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client
    engine.risk_manager = MagicMock()
    engine.risk_manager.remove_pending_order = AsyncMock()
    monkeypatch.setattr("orion.execution.execution_engine.persist_exit_decision", AsyncMock())

    exit_signal = SimpleNamespace(rule_id="ml_exit", reason="stop", urgency="IMMEDIATE", confidence=1.0, details={})
    closed = await engine.close_position(ticker=OCC, qty=10, exit_signal=exit_signal, current_price=5.0)

    assert closed is True
    client.create_order.assert_awaited_once()


async def test_execution_loop_still_runs_exit_rules_while_breaker_open(monkeypatch) -> None:
    """Regression: the loop's breaker gate used to `continue`, skipping the
    exit-rule sweep at the bottom of the iteration."""
    await init_db()
    await CircuitBreaker().open("Drawdown kill switch")

    shutdown = asyncio.Event()
    position = SimpleNamespace(
        ticker=OCC,
        option_chain=OCC,
        qty=10.0,
        direction="LONG",
        candidate_id="c-1",
        current_price=5.0,
    )
    decision = SimpleNamespace(
        decision=DecisionAction.SKIP,
        decision_id="d-1",
        executed_successfully=None,
        reason="skip",
        decision_trace_json=None,
    )

    async def startup_liveness(_shutdown_event: asyncio.Event) -> None:
        await asyncio.Event().wait()

    async def update_status(*_args, **_kwargs) -> None:
        shutdown.set()

    execution_engine = MagicMock()
    execution_engine.acquire_service_lease = AsyncMock()
    execution_engine.initialize = AsyncMock()
    execution_engine.poll_fills = AsyncMock()
    execution_engine.close_position = AsyncMock(return_value=True)
    execution_engine.gateway_positions_snapshot = {}

    signal_engine = MagicMock()
    signal_engine.initialize = AsyncMock()
    signal_engine.decide = AsyncMock(return_value=decision)

    position_manager = MagicMock()
    position_manager.initialize = AsyncMock()
    position_manager.get_open_positions.return_value = [position]
    position_manager.is_closing.return_value = False
    position_manager.mark_closing.return_value = True

    exit_rule = MagicMock()
    exit_rule.should_exit.return_value = SimpleNamespace(rule_id="stop_loss", reason="stop", urgency="IMMEDIATE")

    monkeypatch.setattr(main_execution, "wait_for_db", AsyncMock())
    monkeypatch.setattr(main_execution, "init_db", AsyncMock())
    monkeypatch.setattr(main_execution, "reconcile_orphaned_decisions", AsyncMock())
    monkeypatch.setattr(
        main_execution,
        "ensure_active_solvers_ready",
        AsyncMock(return_value=SimpleNamespace(active_solver_count=1, seeded=False, baseline_solver_id="baseline")),
    )
    monkeypatch.setattr(main_execution, "ExecutionEngine", MagicMock(return_value=execution_engine))
    monkeypatch.setattr(main_execution, "SignalEngine", MagicMock(return_value=signal_engine))
    monkeypatch.setattr("orion.execution.position_manager.PositionManager", MagicMock(return_value=position_manager))
    monkeypatch.setattr("orion.processing.rules.exit_rules.get_default_exit_rules", MagicMock(return_value=[exit_rule]))
    monkeypatch.setattr(main_execution, "_publish_execution_liveness_until_cancelled", startup_liveness)
    monkeypatch.setattr(main_execution, "auto_skip_stale_candidates", AsyncMock())
    monkeypatch.setattr(main_execution, "fetch_pending_candidates", AsyncMock(return_value=[_candidate()]))
    monkeypatch.setattr(main_execution, "save_decision", AsyncMock())
    monkeypatch.setattr(main_execution, "update_decision_status", AsyncMock(side_effect=update_status))
    monkeypatch.setattr(main_execution, "fetch_recent_flow_for_ticker", AsyncMock(return_value=[]))
    monkeypatch.setattr(main_execution, "publish_liveness", AsyncMock())

    await asyncio.wait_for(main_execution.run_execution_service(shutdown), timeout=2.0)

    execution_engine.close_position.assert_awaited_once()
    assert execution_engine.close_position.await_args.kwargs["ticker"] == OCC


async def test_execution_loop_runs_exit_rules_on_a_quiet_cycle(monkeypatch) -> None:
    """An empty candidate pool is the steady state after a halt — the breaker
    SKIPs every candidate, the pool drains, and it stays drained. The exit
    sweep must still run on those cycles, so the loop must not `continue` past
    it when there is nothing to enter."""
    await init_db()
    await CircuitBreaker().open("Drawdown kill switch")

    shutdown = asyncio.Event()
    position = SimpleNamespace(
        ticker=OCC,
        option_chain=OCC,
        qty=10.0,
        direction="LONG",
        candidate_id="c-1",
        current_price=5.0,
    )

    async def startup_liveness(_shutdown_event: asyncio.Event) -> None:
        await asyncio.Event().wait()

    async def closed(**_kwargs) -> bool:
        shutdown.set()
        return True

    execution_engine = MagicMock()
    execution_engine.acquire_service_lease = AsyncMock()
    execution_engine.initialize = AsyncMock()
    execution_engine.poll_fills = AsyncMock()
    execution_engine.close_position = AsyncMock(side_effect=closed)
    execution_engine.gateway_positions_snapshot = {}

    signal_engine = MagicMock()
    signal_engine.initialize = AsyncMock()

    position_manager = MagicMock()
    position_manager.initialize = AsyncMock()
    position_manager.get_open_positions.return_value = [position]
    position_manager.is_closing.return_value = False
    position_manager.mark_closing.return_value = True

    exit_rule = MagicMock()
    exit_rule.should_exit.return_value = SimpleNamespace(rule_id="stop_loss", reason="stop", urgency="IMMEDIATE")

    monkeypatch.setattr(main_execution, "wait_for_db", AsyncMock())
    monkeypatch.setattr(main_execution, "init_db", AsyncMock())
    monkeypatch.setattr(main_execution, "reconcile_orphaned_decisions", AsyncMock())
    monkeypatch.setattr(
        main_execution,
        "ensure_active_solvers_ready",
        AsyncMock(return_value=SimpleNamespace(active_solver_count=1, seeded=False, baseline_solver_id="baseline")),
    )
    monkeypatch.setattr(main_execution, "ExecutionEngine", MagicMock(return_value=execution_engine))
    monkeypatch.setattr(main_execution, "SignalEngine", MagicMock(return_value=signal_engine))
    monkeypatch.setattr("orion.execution.position_manager.PositionManager", MagicMock(return_value=position_manager))
    monkeypatch.setattr("orion.processing.rules.exit_rules.get_default_exit_rules", MagicMock(return_value=[exit_rule]))
    monkeypatch.setattr(main_execution, "_publish_execution_liveness_until_cancelled", startup_liveness)
    monkeypatch.setattr(main_execution, "auto_skip_stale_candidates", AsyncMock())
    monkeypatch.setattr(main_execution, "fetch_pending_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr(main_execution, "fetch_recent_flow_for_ticker", AsyncMock(return_value=[]))
    monkeypatch.setattr(main_execution, "publish_liveness", AsyncMock())

    await asyncio.wait_for(main_execution.run_execution_service(shutdown), timeout=2.0)

    execution_engine.close_position.assert_awaited_once()


async def test_breaker_opened_after_preflight_still_fences_the_submission() -> None:
    """`_pre_flight_checks` runs, then the option chain is fetched over the
    network, then the order is submitted. A breaker opened by another process
    during that gap must still stop the order, so the submission authority
    re-reads the breaker itself rather than trusting the earlier verdict."""
    await init_db()
    await CircuitBreaker().close()
    await _seed_healthy_system()

    engine = _entry_engine()
    decision = _decision()
    candidate = _candidate()

    # Preflight passes against a closed breaker.
    assert await engine._pre_flight_checks(decision, candidate) is True

    # ...and the breaker opens while the option chain is being fetched.
    await CircuitBreaker().open("Manual halt")

    client = MagicMock()
    client.create_order = AsyncMock()
    engine._get_gateway_client = lambda: client

    await engine._submit_options_order(decision, candidate, num_contracts=1, option_price=1.0)

    client.create_order.assert_not_awaited()


async def test_open_survives_a_concurrent_first_insert(monkeypatch) -> None:
    """With no breaker row yet, two processes can both find nothing to update
    and both insert. The loser must resolve to "already open", not raise into
    the drawdown kill switch."""
    from sqlalchemy.exc import IntegrityError

    await init_db()
    breaker = CircuitBreaker()

    async with async_session_factory() as session:
        await session.execute(delete(SystemStatus).where(SystemStatus.key == CircuitBreaker.KEY))
        await session.commit()

    real_db_write = circuit_breaker_module.db_write
    calls = {"n": 0}

    async def racing_db_write(fn):
        calls["n"] += 1
        if calls["n"] == 1:
            # The competing process committed its own insert first.
            async with async_session_factory() as session:
                session.add(
                    SystemStatus(
                        key=CircuitBreaker.KEY,
                        status="OPEN",
                        details="Opened by the other process",
                        last_updated_utc=datetime.now(UTC),
                    )
                )
                await session.commit()
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        return await real_db_write(fn)

    monkeypatch.setattr(circuit_breaker_module, "db_write", racing_db_write)

    await breaker.open("Drawdown kill switch")

    assert await breaker.is_open() is True
    assert (await breaker.get_state())["reason"] == "Opened by the other process"


async def test_stale_healthy_cache_cannot_admit_an_entry_after_the_breaker_opens() -> None:
    """`_check_system_health` caches its verdict for 10s. The breaker is opened
    by other processes (operator, drawdown kill switch), so honouring a cached
    "healthy" for 10s after that would admit entries into a halted system. The
    breaker row must be read on every entry, uncached."""
    import time

    await init_db()
    await CircuitBreaker().close()

    engine = _entry_engine()
    engine._health_cache = (True, time.monotonic())

    await CircuitBreaker().open("Drawdown kill switch")

    assert await engine._pre_flight_checks(_decision(), _candidate()) is False


async def test_execution_loop_skips_entries_while_breaker_open(monkeypatch) -> None:
    """The same iteration that exits must not submit a new entry."""
    await init_db()
    await CircuitBreaker().open("Drawdown kill switch")

    engine = _entry_engine()
    engine._submit_options_order = AsyncMock()
    decision = _decision()
    candidate = _candidate()
    candidate.timestamp_utc = datetime.now(UTC) - timedelta(seconds=1)

    await engine.execute_order(decision, candidate)

    engine._submit_options_order.assert_not_awaited()
