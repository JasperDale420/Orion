"""A per-bucket entry halt stops new entries and never blocks an exit.

The measurement loop's `consider_halting` verdict is an ENTRY gate, deliberately
narrower than the circuit breaker: it lives in `preflight_live_signal`, which
only runs on the candidate → order path. Nothing on the risk-reducing path may
consult it — a bucket halted for negative expectancy still has open positions
that have to be able to get flat, and stranding them would turn a sizing
decision into an unbounded loss.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from orion.execution.execution_engine import ExecutionEngine
from orion.execution.signal_preflight import preflight_live_signal
from orion.jobs.bucket_halt import record_halt
from orion.storage.db import async_session_factory, init_db

pytestmark = pytest.mark.asyncio

OCC = "AAPL260821C00250000"
NOW = datetime(2026, 8, 17, 14, 0, tzinfo=UTC)


async def _halt_every_bucket() -> None:
    for bucket in ("0DTE", "SHORT_SWING", "SWING", "POSITION"):
        await record_halt(bucket, profit_factor=0.3, n_closed=60, now=NOW)


async def test_entry_preflight_rejects_while_the_bucket_is_halted() -> None:
    from orion.config import system_settings
    from orion.storage.models_gold import CandidateTrade, StrategyDecision

    system_settings.require_rollups_for_signals_live = False
    await init_db()
    await _halt_every_bucket()

    candidate = CandidateTrade(
        candidate_id="cand_x",
        ticker="AAPL",
        timestamp_utc=NOW,
        rule_id="rule_swing_v2",
        direction="LONG",
        confidence=0.7,
        source="UW",
        expiration_date=NOW,  # 0DTE
        execution_params={"limit_price": 5.0},
        evidence={"rollup_ids": []},
    )
    decision = StrategyDecision(
        decision_id="dec_x",
        candidate_id="cand_x",
        timestamp_utc=NOW,
        ticker="AAPL",
        strategy_version_id="baseline",
        model_version=None,
        decision="EXECUTE",
        reason="ok",
        executed_successfully="PENDING",
        execution_params={"limit_price": 5.0},
        decision_trace_json={},
    )

    risk = MagicMock()
    risk.calculate_size.return_value = 1.0
    risk.check_order.return_value = True

    async with async_session_factory() as session:
        result = await preflight_live_signal(
            session, candidate=candidate, decision=decision, risk_manager=risk, now_utc=NOW
        )

    assert result.ok is False
    assert result.reason.startswith("Bucket halted by measurement loop: 0DTE")


async def test_close_position_submits_while_every_bucket_is_halted(monkeypatch) -> None:
    """Getting flat is always allowed. The halt must not reach the exit path."""
    await init_db()
    await _halt_every_bucket()

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
