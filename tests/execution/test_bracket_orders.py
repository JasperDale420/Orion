"""
Tests for bracket order placement (stop-loss / take-profit) after entry.

Verifies:
- Bracket orders placed when enable_bracket_orders=True
- Bracket orders NOT placed when enable_bracket_orders=False (default)
- Stop/take-profit prices calculated correctly from entry price and percentages
- Bracket order failures don't block the entry
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


def _make_candidate_and_decision(execution_params=None):
    now = datetime.now(UTC)
    candidate = CandidateTrade(
        candidate_id="test_bracket",
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
        candidate_id="test_bracket",
        execution_params=execution_params or {},
    )
    return candidate, decision


@pytest.mark.asyncio
async def test_bracket_orders_placed_when_enabled(mock_env, monkeypatch):
    """When enable_bracket_orders=True, stop-loss and take-profit orders are submitted."""
    monkeypatch.setattr("orion.execution.execution_engine.risk_settings.enable_bracket_orders", True, raising=False)

    engine, mock_client = _make_engine()

    candidate, decision = _make_candidate_and_decision(
        execution_params={"stop_loss_pct": 0.05, "take_profit_pct": 0.10}
    )

    await engine.execute_order(decision, candidate)

    # Entry order + 2 bracket orders = 3 create_order calls
    assert mock_client.create_order.call_count == 3

    bracket_calls = mock_client.create_order.call_args_list[1:]

    # Stop-loss order
    sl_call = bracket_calls[0]
    assert sl_call[1]["order_type"] == "stop"
    assert sl_call[1]["side"] == "sell"
    assert sl_call[1]["stop_price"] == round(2.0 * (1 - 0.05), 2)

    # Take-profit order
    tp_call = bracket_calls[1]
    assert tp_call[1]["order_type"] == "limit"
    assert tp_call[1]["side"] == "sell"
    assert tp_call[1]["limit_price"] == round(2.0 * (1 + 0.10), 2)


@pytest.mark.asyncio
async def test_bracket_orders_are_orion_attributed_and_reduce_only(mock_env, monkeypatch):
    """Bracket SL/TP must carry an orion_ client_order_id and reduce-only
    position_intent (adversarial review 2026-06-05).

    Without an orion_ id the close-path cancel sweep can't cancel a resting
    bracket order before a flatten, so a surviving bracket SELL can later fire
    on a now-flat position as a NAKED SHORT. The orion_ id also attributes the
    bracket fill so its realized P&L reaches the risk manager; reduce-only
    intent blocks an opening fire as defence-in-depth."""
    monkeypatch.setattr("orion.execution.execution_engine.risk_settings.enable_bracket_orders", True, raising=False)
    from orion.execution.execution_engine import ORDER_ID_PREFIX

    engine, mock_client = _make_engine()
    candidate, decision = _make_candidate_and_decision(
        execution_params={"stop_loss_pct": 0.05, "take_profit_pct": 0.10}
    )

    await engine.execute_order(decision, candidate)

    bracket_calls = mock_client.create_order.call_args_list[1:]
    assert len(bracket_calls) == 2
    for call in bracket_calls:
        coid = call[1].get("client_order_id")
        assert coid is not None and coid.startswith(ORDER_ID_PREFIX), (
            f"bracket order must be orion-attributed so the close cancel sweep catches it; got {coid!r}"
        )
        assert call[1].get("position_intent") == "sell_to_close"  # LONG entry → SELL to close


@pytest.mark.asyncio
async def test_no_bracket_orders_when_disabled(mock_env):
    """Default (enable_bracket_orders=False): no stop/take-profit orders placed."""
    engine, mock_client = _make_engine()

    candidate, decision = _make_candidate_and_decision()

    await engine.execute_order(decision, candidate)

    # Only the entry order
    assert mock_client.create_order.call_count == 1


@pytest.mark.asyncio
async def test_bracket_order_failure_does_not_block_entry(mock_env, monkeypatch):
    """If bracket orders fail, the entry still succeeds and protection state is surfaced."""
    monkeypatch.setattr("orion.execution.execution_engine.risk_settings.enable_bracket_orders", True, raising=False)

    engine, mock_client = _make_engine()

    # First call succeeds (entry), subsequent calls fail (brackets)
    mock_client.create_order.side_effect = [
        {"id": "order-123", "status": "accepted"},  # Entry
        RuntimeError("Stop-loss failed"),  # SL bracket
        RuntimeError("Take-profit failed"),  # TP bracket
    ]

    candidate, decision = _make_candidate_and_decision(
        execution_params={"stop_loss_pct": 0.03, "take_profit_pct": 0.06}
    )

    await engine.execute_order(decision, candidate)

    # Entry should still be marked successful
    from orion.core.enums import DecisionStatus

    assert decision.executed_successfully == DecisionStatus.TRUE

    # Protection state must be surfaced on the decision so operators / DB queries
    # can find unprotected positions instead of having to parse logs.
    ep = decision.execution_params
    assert ep.get("position_unprotected") is True
    bracket = ep["bracket_orders"]
    assert bracket["unprotected"] is True
    assert bracket["partial_protection"] is False
    assert bracket["stop_loss"] is None
    assert bracket["take_profit"] is None
    assert any("Stop-loss failed" in r for r in bracket["failure_reasons"])
    assert any("Take-profit failed" in r for r in bracket["failure_reasons"])


@pytest.mark.asyncio
async def test_bracket_stop_loss_only_failure_marks_unprotected(mock_env, monkeypatch):
    """SL fails, TP succeeds → still 'unprotected' (no auto downside exit)."""
    monkeypatch.setattr("orion.execution.execution_engine.risk_settings.enable_bracket_orders", True, raising=False)

    engine, mock_client = _make_engine()
    mock_client.create_order.side_effect = [
        {"id": "entry-1", "status": "accepted"},
        RuntimeError("Stop-loss failed"),
        {"id": "tp-1", "status": "accepted"},
    ]

    candidate, decision = _make_candidate_and_decision(
        execution_params={"stop_loss_pct": 0.03, "take_profit_pct": 0.06}
    )

    await engine.execute_order(decision, candidate)

    ep = decision.execution_params
    assert ep.get("position_unprotected") is True
    assert ep.get("position_partial_protection") is True
    bracket = ep["bracket_orders"]
    assert bracket["unprotected"] is True
    assert bracket["partial_protection"] is True
    assert bracket["stop_loss"] is None
    assert bracket["take_profit"] is not None


@pytest.mark.asyncio
async def test_bracket_take_profit_only_failure_marks_partial_only(mock_env, monkeypatch):
    """TP fails, SL succeeds → partial protection but not 'unprotected'."""
    monkeypatch.setattr("orion.execution.execution_engine.risk_settings.enable_bracket_orders", True, raising=False)

    engine, mock_client = _make_engine()
    mock_client.create_order.side_effect = [
        {"id": "entry-1", "status": "accepted"},
        {"id": "sl-1", "status": "accepted"},
        RuntimeError("Take-profit failed"),
    ]

    candidate, decision = _make_candidate_and_decision(
        execution_params={"stop_loss_pct": 0.03, "take_profit_pct": 0.06}
    )

    await engine.execute_order(decision, candidate)

    ep = decision.execution_params
    assert ep.get("position_unprotected") is None  # downside-protected
    assert ep.get("position_partial_protection") is True
    bracket = ep["bracket_orders"]
    assert bracket["unprotected"] is False
    assert bracket["partial_protection"] is True
    assert bracket["stop_loss"] is not None
    assert bracket["take_profit"] is None


@pytest.mark.asyncio
async def test_bracket_both_legs_succeed_no_protection_flags(mock_env, monkeypatch):
    """Both legs succeed → no protection-warning flags set."""
    monkeypatch.setattr("orion.execution.execution_engine.risk_settings.enable_bracket_orders", True, raising=False)

    engine, mock_client = _make_engine()
    # Default mock returns success for every call

    candidate, decision = _make_candidate_and_decision(
        execution_params={"stop_loss_pct": 0.03, "take_profit_pct": 0.06}
    )

    await engine.execute_order(decision, candidate)

    ep = decision.execution_params
    assert ep.get("position_unprotected") is None
    assert ep.get("position_partial_protection") is None
    bracket = ep["bracket_orders"]
    assert bracket["unprotected"] is False
    assert bracket["partial_protection"] is False
    assert bracket["failure_reasons"] == []


@pytest.mark.asyncio
async def test_bracket_uses_default_stop_loss_when_not_in_params(mock_env, monkeypatch):
    """Falls back to risk_settings.default_stop_loss_pct when not in execution_params."""
    monkeypatch.setattr("orion.execution.execution_engine.risk_settings.enable_bracket_orders", True, raising=False)

    engine, mock_client = _make_engine()

    # No stop_loss_pct in execution_params
    candidate, decision = _make_candidate_and_decision(execution_params={})

    await engine.execute_order(decision, candidate)

    # Should use default_stop_loss_pct = 0.02
    bracket_calls = mock_client.create_order.call_args_list[1:]
    sl_call = bracket_calls[0]
    assert sl_call[1]["stop_price"] == round(2.0 * (1 - 0.02), 2)
