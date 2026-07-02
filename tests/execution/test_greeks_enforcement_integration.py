"""Integration tests proving the execution engine enforces Greeks limits.

The Greeks risk subsystem (`check_options_order` → `_check_greeks_limits`) was
inert in production: the live options path called plain `check_order`, no greek
data ever reached the risk manager, and portfolio greeks stayed permanently 0.0.
These tests pin the wiring:

1. Greeks present + projected portfolio breach → order rejected, broker untouched.
2. Greeks present + within limits → order submitted, intended greeks stashed.
3. Greeks missing + paper/test stage → fail-open (WARN), order proceeds.
4. Greeks missing + live stage + checks enabled → fail-closed, order blocked.
5. Portfolio greeks track reality across fill (open) and close.
"""

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from orion.config import RiskSettings, system_settings
from orion.execution.risk.manager import RiskManager
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
    """These tests' breach math assumes solver-bps sizing (5 contracts) —
    disable the fixed-premium sizing default so the projections stay exact."""
    from orion.config import risk_settings

    monkeypatch.setattr(risk_settings, "fixed_premium_per_trade", 0.0)
    monkeypatch.setattr(risk_settings, "max_contracts_per_trade", 0)


def _chain_contract(symbol, *, bid=1.90, ask=2.10, delta=None, gamma=None, theta=None, vega=None):
    """Build a chain contract dict. Greeks omitted (None) → 'unavailable'."""
    contract = {"contract_symbol": symbol, "bid": bid, "ask": ask}
    if delta is not None:
        contract["delta"] = delta
    if gamma is not None:
        contract["gamma"] = gamma
    if theta is not None:
        contract["theta"] = theta
    if vega is not None:
        contract["vega"] = vega
    return contract


def _make_gateway_client_mock(contract):
    mock = AsyncMock()
    mock.get_clock.return_value = {"is_open": True}
    mock.get_option_chain.return_value = {"contracts": [contract]}
    mock.create_order.return_value = {"id": "order-123", "status": "accepted"}
    return mock


def _make_engine_with_real_risk(contract, risk_config: RiskSettings):
    from orion.execution.execution_engine import ExecutionEngine

    engine = ExecutionEngine()
    engine._check_system_health = AsyncMock(return_value=True)
    engine._gateway_available = True
    engine._gateway_check_ts = datetime.now(UTC)

    mock_client = _make_gateway_client_mock(contract)
    engine._gateway_client = mock_client
    engine._get_gateway_client = lambda: mock_client

    rm = RiskManager(config=risk_config)
    rm.current_equity = 100_000.0
    engine.risk_manager = rm

    return engine, mock_client, rm


def _make_candidate_and_decision():
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
        # risk_per_trade_bps=100 → $1000 budget / ($2.00 * 100) = 5 contracts
        execution_params={"risk_per_trade_bps": 100, "regime_size_multiplier": 1.0},
    )
    return candidate, decision


# ── 1. Greeks present, projected portfolio breach → rejected ───────────────


@pytest.mark.asyncio
async def test_portfolio_gamma_breach_rejects_order(mock_env):
    """An order whose projected portfolio gamma exceeds the limit is rejected,
    and the broker is never called."""
    # 5 contracts × 100 × 0.05 = 25 position gamma; seeded 90 → projected 115 > 100.
    contract = _chain_contract("AAPL260418C00150000", delta=0.05, gamma=0.05, theta=-0.01, vega=0.02)
    config = RiskSettings(
        max_portfolio_gamma=100.0,
        max_portfolio_delta=500.0,
        max_portfolio_vega=200.0,
        max_position_delta=100.0,
        max_position_vega=50.0,
        enable_greeks_checks=True,
    )
    engine, mock_client, rm = _make_engine_with_real_risk(contract, config)
    rm.portfolio_gamma = 90.0  # existing book exposure

    candidate, decision = _make_candidate_and_decision()
    await engine.execute_order(decision, candidate)

    mock_client.create_order.assert_not_called()
    assert decision.executed_successfully.value == "FALSE"
    assert decision.reason == "Risk Rejection"


# ── 2. Greeks present, within limits → submitted + tracked ─────────────────


@pytest.mark.asyncio
async def test_within_greek_limits_submits_and_stashes(mock_env):
    """A within-limits options order is submitted and its intended position
    greeks are stashed for fill-time tracking."""
    contract = _chain_contract("AAPL260418C00150000", delta=0.05, gamma=0.01, theta=-0.01, vega=0.02)
    config = RiskSettings(enable_greeks_checks=True)
    engine, mock_client, rm = _make_engine_with_real_risk(contract, config)

    candidate, decision = _make_candidate_and_decision()
    await engine.execute_order(decision, candidate)

    mock_client.create_order.assert_called_once()
    assert decision.executed_successfully.value == "TRUE"

    # 5 contracts × 100 multiplier
    stashed = rm._intended_position_greeks.get("AAPL")
    assert stashed is not None
    assert stashed["delta"] == pytest.approx(25.0)
    assert stashed["gamma"] == pytest.approx(5.0)
    assert stashed["theta"] == pytest.approx(-5.0)
    assert stashed["vega"] == pytest.approx(10.0)


# ── 3. Greeks missing, paper/test stage → fail-open ────────────────────────


@pytest.mark.asyncio
async def test_missing_greeks_paper_stage_proceeds(mock_env, monkeypatch):
    """When greeks are unavailable and the stage is paper/test, the greek gate
    is skipped (WARN) and the order proceeds — no regression for fixtures that
    carry no greeks."""
    monkeypatch.setattr(system_settings, "orion_stage", "paper")
    contract = _chain_contract("AAPL260418C00150000")  # no greeks
    config = RiskSettings(enable_greeks_checks=True)
    engine, mock_client, rm = _make_engine_with_real_risk(contract, config)

    candidate, decision = _make_candidate_and_decision()
    await engine.execute_order(decision, candidate)

    mock_client.create_order.assert_called_once()
    assert decision.executed_successfully.value == "TRUE"


# ── 4. Greeks missing, live stage + checks enabled → fail-closed ───────────


@pytest.mark.asyncio
async def test_missing_greeks_live_stage_blocks(mock_env, monkeypatch):
    """When greeks are unavailable, the stage is live, and greek checks are
    enabled, the order is blocked and the broker is never called."""
    monkeypatch.setattr(system_settings, "orion_stage", "live")
    contract = _chain_contract("AAPL260418C00150000")  # no greeks
    config = RiskSettings(enable_greeks_checks=True)
    engine, mock_client, rm = _make_engine_with_real_risk(contract, config)

    candidate, decision = _make_candidate_and_decision()
    await engine.execute_order(decision, candidate)

    mock_client.create_order.assert_not_called()
    assert decision.executed_successfully.value == "FALSE"
    assert decision.reason == "Greeks Unavailable"


@pytest.mark.asyncio
async def test_missing_greeks_live_stage_checks_disabled_proceeds(mock_env, monkeypatch):
    """Live stage but greek checks disabled: missing greeks must NOT block —
    the operator opted out of greek enforcement entirely."""
    monkeypatch.setattr(system_settings, "orion_stage", "live")
    contract = _chain_contract("AAPL260418C00150000")  # no greeks
    config = RiskSettings(enable_greeks_checks=False)
    engine, mock_client, rm = _make_engine_with_real_risk(contract, config)

    candidate, decision = _make_candidate_and_decision()
    await engine.execute_order(decision, candidate)

    mock_client.create_order.assert_called_once()
    assert decision.executed_successfully.value == "TRUE"


# ── 5. Portfolio greeks track across fill → close ──────────────────────────


@pytest.mark.asyncio
async def test_portfolio_greeks_tracked_across_fill_and_close(risk_manager_factory):
    """process_fill applies intended greeks on an opening fill and clears them
    when the position is closed."""
    rm = risk_manager_factory()
    rm.current_equity = 100_000.0

    rm.set_intended_position_greeks("AAPL", delta=50.0, gamma=5.0, theta=-2.0, vega=30.0)

    # Opening buy fill → portfolio greeks reflect the position.
    await rm.process_fill("AAPL", 5, 2.0, "buy", fill_id="open_1")
    assert rm.portfolio_delta == pytest.approx(50.0)
    assert rm.portfolio_gamma == pytest.approx(5.0)
    assert rm.portfolio_vega == pytest.approx(30.0)
    assert "AAPL" in rm.position_greeks

    # Closing sell fill (flattens) → greeks cleared.
    await rm.process_fill("AAPL", 5, 2.5, "sell", fill_id="close_1")
    assert rm.portfolio_delta == pytest.approx(0.0)
    assert rm.portfolio_gamma == pytest.approx(0.0)
    assert rm.portfolio_vega == pytest.approx(0.0)
    assert "AAPL" not in rm.position_greeks
