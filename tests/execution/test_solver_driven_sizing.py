"""
Tests for solver-driven position sizing in ExecutionEngine.

Verifies that execution sizing uses risk_per_trade_bps × regime_size_multiplier
from execution_params when present, and caps at max_option_premium_pct.
"""

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.storage.models_gold import CandidateTrade, StrategyDecision


@pytest.fixture
def mock_env():
    with patch.dict(
        os.environ,
        {
            "ALPACA_API_KEY": "test_key",  # pragma: allowlist secret
            "ALPACA_SECRET_KEY": "test_secret",  # pragma: allowlist secret
            "ALPACA_PAPER": "True",
        },
    ):
        yield


@pytest.fixture(autouse=True)
def bps_sizing(monkeypatch):
    """These tests pin the solver-bps sizing path — disable the fixed-premium
    default (individual tests re-enable it to pin the fixed-premium policy)."""
    from orion.config import risk_settings

    monkeypatch.setattr(risk_settings, "fixed_premium_per_trade", 0.0)
    monkeypatch.setattr(risk_settings, "max_contracts_per_trade", 0)


def _make_gateway_client_mock():
    mock = AsyncMock()
    mock.get_clock.return_value = {"is_open": True}
    mock.get_option_chain.return_value = {
        "contracts": [{"contract_symbol": "AAPL260418C00150000", "bid": 1.90, "ask": 2.10}]
    }
    mock.create_order.return_value = {"id": "order-123", "status": "accepted"}
    return mock


def _make_engine(equity: float = 100_000.0):
    from orion.execution.execution_engine import ExecutionEngine

    engine = ExecutionEngine()
    engine._check_system_health = AsyncMock(return_value=True)
    engine._gateway_available = True
    engine._gateway_check_ts = datetime.now(UTC)

    mock_client = _make_gateway_client_mock()
    engine._gateway_client = mock_client
    engine._get_gateway_client = lambda: mock_client

    engine.risk_manager = MagicMock()
    engine.risk_manager.config.enable_shorting = False
    engine.risk_manager.ticker_exposures = {}
    engine.risk_manager.current_equity = equity
    engine.risk_manager.check_order.return_value = True
    engine.risk_manager.update_post_trade = AsyncMock()
    engine.risk_manager.remove_pending_order = AsyncMock()

    return engine, mock_client


def _make_candidate_and_decision(execution_params=None):
    now = datetime.now(UTC)
    candidate = CandidateTrade(
        candidate_id="test_id",
        ticker="AAPL",
        timestamp_utc=now,
        rule_id="test_rule",
        direction="LONG",
        evidence={},
        option_symbol="AAPL260418C00150000",
        premium=2.0,
        expiration_date=now + timedelta(days=30),
    )
    decision = StrategyDecision(
        decision="EXECUTE",
        timestamp_utc=now,
        strategy_version_id="test",
        ticker="AAPL",
        candidate_id="test",
        execution_params=execution_params or {},
    )
    return candidate, decision


@pytest.mark.asyncio
async def test_solver_driven_sizing_uses_risk_bps(mock_env):
    """When risk_per_trade_bps is in execution_params, sizing uses it."""
    engine, mock_client = _make_engine(equity=100_000.0)

    # risk_per_trade_bps=100 means 1% = $1000 risk budget
    # regime_mult=1.0 (no adjustment)
    # option_price=2.0, so per contract = $200 → $1000/$200 = 5 contracts
    candidate, decision = _make_candidate_and_decision(
        execution_params={"risk_per_trade_bps": 100, "regime_size_multiplier": 1.0}
    )

    await engine.execute_order(decision, candidate)

    mock_client.create_order.assert_called_once()
    call_kwargs = mock_client.create_order.call_args[1]
    assert int(call_kwargs["qty"]) == 5
    # State effects: sizing alone isn't enough — the order must have actually
    # gone through. Pin the decision outcome, the risk-reservation, and the
    # breaker bookkeeping so a regression that sizes right but fails/skips the
    # submit (or skips reserving pending risk) is caught.
    assert decision.executed_successfully == "TRUE"
    engine.risk_manager.update_post_trade.assert_awaited_once()
    assert engine.order_history[-1][1] is True


@pytest.mark.asyncio
async def test_regime_multiplier_reduces_size(mock_env):
    """regime_size_multiplier < 1 reduces contract count."""
    engine, mock_client = _make_engine(equity=100_000.0)

    # risk_bps=100 → $1000 base, regime_mult=0.5 → $500
    # $500 / $200 per contract = 2 contracts
    candidate, decision = _make_candidate_and_decision(
        execution_params={"risk_per_trade_bps": 100, "regime_size_multiplier": 0.5}
    )

    await engine.execute_order(decision, candidate)

    mock_client.create_order.assert_called_once()
    call_kwargs = mock_client.create_order.call_args[1]
    assert int(call_kwargs["qty"]) == 2
    # The reduced-size order still completed end-to-end.
    assert decision.executed_successfully == "TRUE"
    assert engine.order_history[-1][1] is True


@pytest.mark.asyncio
async def test_sizing_capped_at_max_premium(mock_env):
    """Solver-driven sizing never exceeds max_option_premium_pct safety ceiling."""
    engine, mock_client = _make_engine(equity=100_000.0)

    # risk_bps=500 → $5000, but max_option_premium_pct=0.02 → $2000 ceiling
    # $2000 / $200 per contract = 10 contracts
    candidate, decision = _make_candidate_and_decision(
        execution_params={"risk_per_trade_bps": 500, "regime_size_multiplier": 1.0}
    )

    await engine.execute_order(decision, candidate)

    mock_client.create_order.assert_called_once()
    call_kwargs = mock_client.create_order.call_args[1]
    # Should be capped at 10 (max_option_premium_pct=0.02 * 100000 / 200 = 10)
    assert int(call_kwargs["qty"]) <= 10
    # The capped order still completed end-to-end.
    assert decision.executed_successfully == "TRUE"
    assert engine.order_history[-1][1] is True


@pytest.mark.asyncio
async def test_fallback_to_flat_sizing_without_risk_bps(mock_env):
    """Without risk_per_trade_bps, falls back to max_option_premium_pct flat sizing."""
    engine, mock_client = _make_engine(equity=100_000.0)

    # No risk_bps → falls back to max_option_premium_pct=0.02 → $2000
    # $2000 / $200 = 10 contracts
    candidate, decision = _make_candidate_and_decision(execution_params={})

    await engine.execute_order(decision, candidate)

    mock_client.create_order.assert_called_once()
    call_kwargs = mock_client.create_order.call_args[1]
    assert int(call_kwargs["qty"]) == 10
    # The flat-sized fallback order still completed end-to-end.
    assert decision.executed_successfully == "TRUE"
    assert engine.order_history[-1][1] is True


@pytest.mark.asyncio
async def test_fixed_premium_sizing_overrides_solver_bps(mock_env, monkeypatch):
    """With fixed_premium_per_trade set (the default), sizing ignores solver
    bps: every trade risks the same fixed debit for uniform sample weights."""
    from orion.config import risk_settings

    monkeypatch.setattr(risk_settings, "fixed_premium_per_trade", 500.0)
    engine, mock_client = _make_engine(equity=100_000.0)

    # $500 fixed / ($2.00 * 100) = 2 contracts, regardless of risk_bps=100.
    candidate, decision = _make_candidate_and_decision(
        execution_params={"risk_per_trade_bps": 100, "regime_size_multiplier": 1.0}
    )

    await engine.execute_order(decision, candidate)

    mock_client.create_order.assert_called_once()
    assert int(mock_client.create_order.call_args[1]["qty"]) == 2
    assert decision.executed_successfully == "TRUE"


@pytest.mark.asyncio
async def test_max_contracts_per_trade_caps_cheap_contracts(mock_env, monkeypatch):
    """A cheap contract can't balloon into a huge lot: contracts cap applies."""
    from orion.config import risk_settings

    monkeypatch.setattr(risk_settings, "fixed_premium_per_trade", 500.0)
    monkeypatch.setattr(risk_settings, "max_contracts_per_trade", 5)
    engine, mock_client = _make_engine(equity=100_000.0)
    # $0.25 mid → $500 / $25 = 20 contracts uncapped → capped at 5.
    engine._gateway_client.get_option_chain.return_value = {
        "contracts": [{"contract_symbol": "AAPL260418C00150000", "bid": 0.24, "ask": 0.26}]
    }

    candidate, decision = _make_candidate_and_decision(execution_params={})

    await engine.execute_order(decision, candidate)

    mock_client.create_order.assert_called_once()
    assert int(mock_client.create_order.call_args[1]["qty"]) == 5
    assert decision.executed_successfully == "TRUE"
