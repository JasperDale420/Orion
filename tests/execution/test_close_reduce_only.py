"""close_position is strictly reduce-only against the LIVE broker position.

Root cause (2026-05-29): position_monitor passed a stale/wrong tracked qty into
close_position, which trusted it and submitted a SELL. When no long actually
existed at the broker, Alpaca treated the sell as OPENING a cash-secured short
put (code 40310000) — rejected, retried every 60s (~3,235/day), and in cases
where buying power existed, it FILLED, flipping Orion into naked short puts
(fills showed sold > bought; some contracts sold with zero buys).

The fix: re-verify the live broker position at submit time and only ever
REDUCE it — sell only when long, buy-to-cover only when short, cap to the held
qty, and refuse to submit when the position can't be confirmed.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from orion.core.enums import OrderSide
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
    client.create_order = AsyncMock(return_value={"id": "o1", "status": "accepted"})
    ee._gateway_client = client
    ee._get_gateway_client = lambda: client
    ee.risk_manager = MagicMock()

    # Avoid DB writes from the success path.
    monkeypatch.setattr("orion.execution.execution_engine.persist_exit_decision", AsyncMock())
    return ee, client


@pytest.mark.asyncio
async def test_long_position_sells_to_close(monkeypatch):
    ee, client = _engine({"symbol": OCC, "qty": "10"}, monkeypatch)
    ok = await ee.close_position(ticker=OCC, qty=10, exit_signal=_exit_signal(), current_price=5.0)
    assert ok is True
    client.create_order.assert_awaited_once()
    kw = client.create_order.await_args.kwargs
    assert kw["side"] == OrderSide.SELL
    assert kw["qty"] == 10


@pytest.mark.asyncio
async def test_flat_broker_does_not_open_short(monkeypatch):
    """THE regression: tracker thinks long 10, but the broker holds nothing.
    Selling would OPEN a naked short put — must be refused."""
    ee, client = _engine({"error": "position does not exist"}, monkeypatch)
    ok = await ee.close_position(ticker=OCC, qty=10, exit_signal=_exit_signal(), current_price=5.0)
    assert ok is False
    client.create_order.assert_not_called()


@pytest.mark.asyncio
async def test_short_position_buys_to_cover(monkeypatch):
    ee, client = _engine({"symbol": OCC, "qty": "-8"}, monkeypatch)
    # Tracker passes a (wrong) positive qty; the broker sign must win.
    ok = await ee.close_position(ticker=OCC, qty=8, exit_signal=_exit_signal(), current_price=5.0)
    assert ok is True
    kw = client.create_order.await_args.kwargs
    assert kw["side"] == OrderSide.BUY
    assert kw["qty"] == 8


@pytest.mark.asyncio
async def test_caps_sell_qty_to_held_long(monkeypatch):
    """Tracker says 10 but the broker only holds 5 long → sell at most 5."""
    ee, client = _engine({"symbol": OCC, "qty": "5"}, monkeypatch)
    ok = await ee.close_position(ticker=OCC, qty=10, exit_signal=_exit_signal(), current_price=5.0)
    assert ok is True
    assert client.create_order.await_args.kwargs["qty"] == 5


@pytest.mark.asyncio
async def test_unverifiable_position_is_skipped(monkeypatch):
    """If the live position can't be fetched, fail safe — do NOT submit."""
    ee, client = _engine("raises", monkeypatch)
    ok = await ee.close_position(ticker=OCC, qty=10, exit_signal=_exit_signal(), current_price=5.0)
    assert ok is False
    client.create_order.assert_not_called()
