"""
Tests for narrowing the live option-chain fetch to the candidate's known
contract before pricing it in `_execute_options_order`.

Previously the entry path called `client.get_option_chain(candidate.ticker)`
with no filters to price exactly ONE contract, paging the entire chain
(~6s for SPY) and pushing batch-mates over the freshness budget. The
Gateway route accepts expiration/type/strike filters; the client now
forwards the candidate's known expiry, option type, and a tight strike
window when available.
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


def _make_gateway_mock():
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

    mock_client = _make_gateway_mock()
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


@pytest.mark.asyncio
async def test_execute_options_order_narrows_chain_fetch_to_candidate_contract(mock_env):
    """The chain fetch must carry the candidate's known expiry, option type,
    and a tight strike window — not fetch the full chain — since it's
    pricing exactly one already-identified contract."""
    engine, mock_client = _make_engine()

    now = datetime.now(UTC)
    expiry = now + timedelta(days=30)
    candidate = CandidateTrade(
        candidate_id="test_chain_filter",
        ticker="AAPL",
        timestamp_utc=now,
        rule_id="test_rule",
        direction="LONG",
        evidence={},
        option_symbol="AAPL260418C00150000",
        option_type="CALL",
        strike_price=150.0,
        premium=2.0,
        expiration_date=expiry,
    )
    decision = StrategyDecision(
        decision="EXECUTE",
        timestamp_utc=now,
        strategy_version_id="test",
        ticker="AAPL",
        candidate_id="test_chain_filter",
        execution_params={},
    )

    await engine.execute_order(decision, candidate)

    assert mock_client.get_option_chain.call_count == 1
    call = mock_client.get_option_chain.call_args
    assert call.args[0] == "AAPL"
    assert call.kwargs["expiration_date"] == expiry.date().isoformat()
    assert call.kwargs["option_type"] == "call"
    assert call.kwargs["strike_price_gte"] is not None
    assert call.kwargs["strike_price_gte"] < 150.0
    assert call.kwargs["strike_price_lte"] is not None
    assert call.kwargs["strike_price_lte"] > 150.0


def _make_candidate_and_decision(candidate_id: str, *, option_type=None, strike_price=None):
    now = datetime.now(UTC)
    expiry = now + timedelta(days=30)
    candidate = CandidateTrade(
        candidate_id=candidate_id,
        ticker="AAPL",
        timestamp_utc=now,
        rule_id="test_rule",
        direction="LONG",
        evidence={},
        option_symbol="AAPL260418C00150000",
        option_type=option_type,
        strike_price=strike_price,
        premium=2.0,
        expiration_date=expiry,
    )
    decision = StrategyDecision(
        decision="EXECUTE",
        timestamp_utc=now,
        strategy_version_id="test",
        ticker="AAPL",
        candidate_id=candidate_id,
        execution_params={},
    )
    return candidate, decision, expiry


@pytest.mark.asyncio
async def test_execute_options_order_omits_all_filters_when_candidate_lacks_type_and_strike(mock_env):
    """A candidate missing BOTH option_type and strike_price must fetch the
    fully unfiltered chain — including no `expiration_date` filter — so an
    incomplete candidate is never at risk of a partial filter excluding the
    true contract before the exact contract_symbol match runs. Filtering is
    all-or-nothing, not applied axis-by-axis."""
    engine, mock_client = _make_engine()
    candidate, decision, _expiry = _make_candidate_and_decision("test_chain_no_filter")

    await engine.execute_order(decision, candidate)

    call = mock_client.get_option_chain.call_args
    assert call.kwargs["expiration_date"] is None
    assert call.kwargs["option_type"] is None
    assert call.kwargs["strike_price_gte"] is None
    assert call.kwargs["strike_price_lte"] is None


@pytest.mark.asyncio
async def test_execute_options_order_omits_all_filters_when_candidate_lacks_strike_only(mock_env):
    """A candidate with option_type but no strike_price must also fall back
    to the fully unfiltered fetch — partial filtering (e.g. type without a
    strike bound) is not applied."""
    engine, mock_client = _make_engine()
    candidate, decision, _expiry = _make_candidate_and_decision("test_chain_no_strike", option_type="CALL")

    await engine.execute_order(decision, candidate)

    call = mock_client.get_option_chain.call_args
    assert call.kwargs["expiration_date"] is None
    assert call.kwargs["option_type"] is None
    assert call.kwargs["strike_price_gte"] is None
    assert call.kwargs["strike_price_lte"] is None


@pytest.mark.asyncio
async def test_execute_options_order_omits_all_filters_when_candidate_lacks_type_only(mock_env):
    """A candidate with strike_price but no option_type must also fall back
    to the fully unfiltered fetch."""
    engine, mock_client = _make_engine()
    candidate, decision, _expiry = _make_candidate_and_decision("test_chain_no_type", strike_price=150.0)

    await engine.execute_order(decision, candidate)

    call = mock_client.get_option_chain.call_args
    assert call.kwargs["expiration_date"] is None
    assert call.kwargs["option_type"] is None
    assert call.kwargs["strike_price_gte"] is None
    assert call.kwargs["strike_price_lte"] is None
