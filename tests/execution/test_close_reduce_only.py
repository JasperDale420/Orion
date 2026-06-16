"""close_position is strictly reduce-only against the LIVE broker position.

Root cause (2026-05-29): position_monitor passed a stale/wrong tracked qty into
close_position, which trusted it and submitted a SELL. When no long actually
existed at the broker, Alpaca treated the sell as OPENING a cash-secured short
put (code 40310000) — rejected, retried every 60s (~3,235/day), and in cases
where buying power existed, it FILLED, flipping Orion into naked short puts.

The fix: re-verify the live broker position at submit time and only ever
REDUCE it — sell only when long, buy-to-cover only when short, cap to the held
qty, and refuse to submit when the position can't be confirmed. The primary
close is an orion-attributed LIMIT (so its fill feeds PnL/kill-switch); the
side derives from the broker qty sign, not the caller's direction hint.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from orion.execution.execution_engine import ExecutionEngine

OCC = "AAPL260529P00315000"


def _exit_signal():
    return SimpleNamespace(rule_id="ml_exit", reason="expiry", urgency="IMMEDIATE", confidence=1.0, details={})


def _engine(broker_position, monkeypatch):
    ee = ExecutionEngine()
    ee._gateway_available = True
    ee._gateway_check_ts = datetime.now(UTC)
    ee._check_gateway_available = AsyncMock(return_value=True)
    ms = MagicMock()
    ms.is_market_open_for_options.return_value = True
    ee._market_schedule = ms

    client = AsyncMock()
    if broker_position == "raises":
        client.get_position = AsyncMock(side_effect=RuntimeError("gateway down"))
    else:
        client.get_position = AsyncMock(return_value=broker_position)
    # Primary close is the orion-attributed LIMIT (create_order); it succeeds
    # here so the native escalation (close_position) is only hit by tests that
    # force a limit rejection.
    client.create_order = AsyncMock(return_value={"id": "o1", "status": "accepted"})
    client.close_position = AsyncMock(return_value={"id": "o1", "status": "accepted"})
    client.get_orders = AsyncMock(return_value=[])
    ee._gateway_client = client
    ee._get_gateway_client = lambda: client
    ee.risk_manager = MagicMock()
    ee.risk_manager.remove_pending_order = AsyncMock()

    # Avoid DB writes from the success path.
    monkeypatch.setattr("orion.execution.execution_engine.persist_exit_decision", AsyncMock())
    return ee, client


@pytest.mark.asyncio
async def test_long_position_sells_to_close(monkeypatch):
    """A live long is closed with a SELL limit, bounded to the held qty, with
    reduce-only position_intent. No native escalation when the limit succeeds."""
    ee, client = _engine({"symbol": OCC, "qty": "10", "avg_entry_price": "1.0"}, monkeypatch)
    ok = await ee.close_position(ticker=OCC, qty=10, exit_signal=_exit_signal(), current_price=5.0)
    assert ok is True
    client.create_order.assert_awaited_once()
    kw = client.create_order.await_args.kwargs
    assert kw["side"] == "sell"
    assert kw["qty"] == 10.0
    assert kw["position_intent"] == "sell_to_close"
    client.close_position.assert_not_called()
    # State effect: the limit success was recorded for the circuit breaker.
    assert ee.order_history[-1][1] is True


@pytest.mark.asyncio
async def test_flat_broker_does_not_open_short(monkeypatch):
    """THE regression: tracker thinks long 10, but the broker holds nothing.
    Selling would OPEN a naked short put — must be refused before any submit."""
    ee, client = _engine({"error": "position does not exist"}, monkeypatch)
    ok = await ee.close_position(ticker=OCC, qty=10, exit_signal=_exit_signal(), current_price=5.0)
    assert ok is False
    client.create_order.assert_not_called()
    client.close_position.assert_not_called()
    # State effect: a refused close is NOT a broker round-trip — nothing is
    # recorded to the breaker history (a regression that records a False here
    # would erode the breaker on a no-op safety refusal).
    assert list(ee.order_history) == []


@pytest.mark.asyncio
async def test_short_position_buys_to_cover(monkeypatch):
    ee, client = _engine({"symbol": OCC, "qty": "-8", "avg_entry_price": "5.0"}, monkeypatch)
    # Tracker passes a (wrong) positive qty; the broker sign must win → BUY-to-cover.
    ok = await ee.close_position(ticker=OCC, qty=8, exit_signal=_exit_signal(), current_price=3.0)
    assert ok is True
    kw = client.create_order.await_args.kwargs
    assert kw["side"] == "buy"
    assert kw["qty"] == 8.0
    assert kw["position_intent"] == "buy_to_close"
    assert ee.order_history[-1][1] is True


@pytest.mark.asyncio
async def test_caps_close_qty_to_held_long(monkeypatch):
    """Tracker says 10 but the broker only holds 5 long → close at most 5."""
    ee, client = _engine({"symbol": OCC, "qty": "5", "avg_entry_price": "1.0"}, monkeypatch)
    ok = await ee.close_position(ticker=OCC, qty=10, exit_signal=_exit_signal(), current_price=5.0)
    assert ok is True
    assert client.create_order.await_args.kwargs["qty"] == 5.0


@pytest.mark.asyncio
async def test_unverifiable_position_is_skipped(monkeypatch):
    """If the live position can't be fetched, fail safe — do NOT submit."""
    ee, client = _engine("raises", monkeypatch)
    ok = await ee.close_position(ticker=OCC, qty=10, exit_signal=_exit_signal(), current_price=5.0)
    assert ok is False
    client.create_order.assert_not_called()
    client.close_position.assert_not_called()
    # State effect: an unverifiable position is skipped, not counted as a
    # broker round-trip — breaker history stays empty.
    assert list(ee.order_history) == []
