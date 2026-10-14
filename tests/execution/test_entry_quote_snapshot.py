"""Pre-decision chain snapshot and shadow factors on the entry decision trace."""

import json
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orion.config import system_settings
from orion.core.enums import DecisionStatus
from orion.storage.models_gold import CandidateTrade, StrategyDecision

OPTION_SYMBOL = "AAPL260918C00150000"


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


def _make_gateway_client_mock(contract_extra: dict | None = None):
    contract = {"contract_symbol": OPTION_SYMBOL, "bid": 1.90, "ask": 2.10}
    contract.update(contract_extra or {})
    mock = AsyncMock()
    mock.get_clock.return_value = {"is_open": True}
    mock.get_option_chain.return_value = {"contracts": [contract]}
    mock.create_order.return_value = {"id": "order-123", "status": "accepted"}
    return mock


def _make_engine(mock_client):
    from orion.execution.execution_engine import ExecutionEngine

    engine = ExecutionEngine()
    engine._check_system_health = AsyncMock(return_value=True)
    engine._gateway_available = True
    engine._gateway_check_ts = datetime.now(UTC)
    engine._gateway_client = mock_client
    engine._get_gateway_client = lambda: mock_client

    engine.risk_manager = MagicMock()
    engine.risk_manager.config.enable_shorting = False
    engine.risk_manager.ticker_exposures = {}
    engine.risk_manager.current_equity = 100000.0
    engine.risk_manager.check_order.return_value = True
    engine.risk_manager.update_post_trade = AsyncMock()
    engine.risk_manager.remove_pending_order = AsyncMock()
    return engine


def _make_candidate_and_decision(days_to_expiry: int = 35):
    now = datetime.now(UTC)
    candidate = CandidateTrade(
        candidate_id="test_id",
        ticker="AAPL",
        timestamp_utc=now,
        rule_id="test_rule",
        direction="LONG",
        evidence={},
        option_symbol=OPTION_SYMBOL,
        option_type="CALL",
        strike_price=150.0,
        underlying_price=140.0,
        premium=51_870.0,
        expiration_date=now + timedelta(days=days_to_expiry),
    )
    decision = StrategyDecision(
        decision="EXECUTE",
        timestamp_utc=now,
        strategy_version_id="test",
        ticker="AAPL",
        candidate_id="test",
    )
    return candidate, decision


# --- chain snapshot ---------------------------------------------------------


async def test_entry_quote_carries_the_pre_decision_chain_snapshot(mock_env):
    mock_client = _make_gateway_client_mock(
        {
            "iv": "0.3421",
            "delta": "0.55",
            "gamma": "0.02",
            "theta": "-0.11",
            "vega": "0.09",
            "open_interest": 8344,
            "volume": 2008,
            "underlying_price": "141.25",
            "timestamp": "2026-08-14T19:52:53.243352+00:00",
        }
    )
    engine = _make_engine(mock_client)
    candidate, decision = _make_candidate_and_decision()

    await engine.execute_order(decision, candidate)

    quote = decision.decision_trace_json["entry_quote"]
    assert quote["iv"] == pytest.approx(0.3421)
    assert quote["delta"] == pytest.approx(0.55)
    assert quote["gamma"] == pytest.approx(0.02)
    assert quote["theta"] == pytest.approx(-0.11)
    assert quote["vega"] == pytest.approx(0.09)
    assert quote["open_interest"] == 8344
    assert quote["volume"] == 2008
    assert quote["underlying_price"] == pytest.approx(141.25)
    assert quote["snapshot_ts"] == "2026-08-14T19:52:53.243352+00:00"
    # The pre-existing quote fields are untouched.
    assert quote["bid"] == pytest.approx(1.90)
    assert quote["ask"] == pytest.approx(2.10)
    assert quote["mid"] == pytest.approx(2.00)
    assert "limit_price" in quote and "payup_frac" in quote
    mock_client.create_order.assert_called_once()


async def test_missing_chain_snapshot_fields_are_none_and_never_block_the_order(mock_env):
    engine = _make_engine(_make_gateway_client_mock())
    candidate, decision = _make_candidate_and_decision()

    await engine.execute_order(decision, candidate)

    quote = decision.decision_trace_json["entry_quote"]
    for field in ("iv", "delta", "gamma", "theta", "vega", "open_interest", "volume", "underlying_price"):
        assert quote[field] is None, field
    assert quote["snapshot_ts"] is None
    engine._get_gateway_client().create_order.assert_called_once()


async def test_unparseable_chain_snapshot_values_degrade_to_none(mock_env):
    engine = _make_engine(_make_gateway_client_mock({"iv": "n/a", "open_interest": "", "underlying_price": "NaN"}))
    candidate, decision = _make_candidate_and_decision()

    await engine.execute_order(decision, candidate)

    quote = decision.decision_trace_json["entry_quote"]
    assert quote["iv"] is None
    assert quote["open_interest"] is None
    assert quote["underlying_price"] is None


# --- factors ----------------------------------------------------------------


async def test_decision_trace_carries_json_safe_factors(mock_env):
    engine = _make_engine(_make_gateway_client_mock({"iv": "0.3421", "delta": "0.55"}))
    candidate, decision = _make_candidate_and_decision()

    await engine.execute_order(decision, candidate)

    factors = decision.decision_trace_json["factors"]
    assert factors["f_dte"] == 35
    assert factors["f_bucket"] == "POSITION"
    assert factors["f_abs_delta"] == pytest.approx(0.55)
    assert factors["f_spread_pct"] == pytest.approx(0.10)
    # The whole trace has to survive a PostgreSQL json column, which rejects
    # the bare NaN/Infinity that json.dumps emits by default.
    encoded = json.dumps(decision.decision_trace_json, allow_nan=False)
    assert json.loads(encoded) == decision.decision_trace_json


async def test_a_non_finite_quote_never_reaches_the_decision_trace(mock_env):
    # An infinite bid/ask clears "two-sided" and every spread cap (a NaN spread
    # compares false against all of them), then overflows the tick rounding and
    # writes Infinity/NaN into the trace that the json column has to store.
    engine = _make_engine(_make_gateway_client_mock({"bid": "Infinity", "ask": "Infinity"}))
    candidate, decision = _make_candidate_and_decision()

    await engine.execute_order(decision, candidate)

    engine._get_gateway_client().create_order.assert_not_called()
    assert decision.reason == "Option Price Fetch Failed"
    assert "entry_quote" not in (decision.decision_trace_json or {})


async def test_factor_failure_never_blocks_the_order(mock_env):
    engine = _make_engine(_make_gateway_client_mock())
    candidate, decision = _make_candidate_and_decision()

    with patch(
        "orion.execution.execution_engine.compute_candidate_factors",
        AsyncMock(side_effect=RuntimeError("factors exploded")),
    ):
        await engine.execute_order(decision, candidate)

    engine._get_gateway_client().create_order.assert_called_once()
    assert decision.decision_trace_json["factors"] == {}


# --- gate -------------------------------------------------------------------


async def test_factor_gate_is_off_by_default_and_the_order_goes_through(mock_env):
    assert system_settings.factor_gates == {}
    engine = _make_engine(_make_gateway_client_mock())
    candidate, decision = _make_candidate_and_decision()

    await engine.execute_order(decision, candidate)

    engine._get_gateway_client().create_order.assert_called_once()
    assert decision.executed_successfully != DecisionStatus.SKIPPED


async def test_factor_gate_skips_a_candidate_outside_its_band(mock_env):
    engine = _make_engine(_make_gateway_client_mock())
    candidate, decision = _make_candidate_and_decision(days_to_expiry=35)

    with patch.object(system_settings, "factor_gates", {"f_dte": {"max": 30}}):
        await engine.execute_order(decision, candidate)

    engine._get_gateway_client().create_order.assert_not_called()
    assert decision.executed_successfully == DecisionStatus.SKIPPED
    assert decision.reason == "Factor gate: f_dte=35.000 outside [None,30]"
    # The trace still records what was measured, so a gated SKIP is auditable.
    assert decision.decision_trace_json["factors"]["f_dte"] == 35


async def test_factor_gate_does_not_fire_on_an_uncomputable_factor(mock_env):
    engine = _make_engine(_make_gateway_client_mock())
    candidate, decision = _make_candidate_and_decision()

    # No daily closes exist in the test DB, so f_vrp cannot be computed.
    with patch.object(system_settings, "factor_gates", {"f_vrp": {"min": 0.0}}):
        await engine.execute_order(decision, candidate)

    assert decision.decision_trace_json["factors"]["f_vrp"] is None
    engine._get_gateway_client().create_order.assert_called_once()
    assert decision.executed_successfully != DecisionStatus.SKIPPED
