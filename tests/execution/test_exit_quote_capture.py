"""The options close path records the market it closed into.

Entry side already persists `decision_trace_json.entry_quote`; without the exit
side there is no way to measure what the close actually cost (slippage past the
stop, realized effective spread) — the dominant cost term for short-dated options.

The capture is DATA ONLY. It reuses the quote `close_position` already fetches to
price its limit — no extra round-trip and no await added anywhere on the close
path — and any failure in it must leave the close itself untouched.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

import orion.execution.execution_engine as ee_mod
from orion.execution.execution_engine import ExecutionEngine

pytestmark = pytest.mark.unit

OPTION = "NVDA260522C00250000"


def _make_engine() -> ExecutionEngine:
    engine = ExecutionEngine.__new__(ExecutionEngine)
    engine._gateway_available = True
    engine._gateway_check_ts = datetime.now(UTC)
    engine.risk_manager = Mock()
    engine.risk_manager.remove_pending_order = AsyncMock()
    engine.order_history = []
    engine.last_positions_snapshot_ts = None
    engine._ledger = None
    engine._check_gateway_available = AsyncMock(return_value=True)
    engine._record_result = Mock()
    engine._market_schedule = MagicMock()
    engine._market_schedule.is_market_open_for_options = Mock(return_value=True)
    return engine


def _make_client(*, quote: object = None, quote_raises: bool = False) -> AsyncMock:
    client = AsyncMock()
    client.get_position = AsyncMock(return_value={"qty": "10", "avg_entry_price": "1.0"})
    client.get_orders = AsyncMock(return_value=[])
    client.cancel_order = AsyncMock(return_value={"status": "canceled"})
    client.create_order = AsyncMock(return_value={"id": "broker-close-1", "status": "accepted"})
    client.close_position = AsyncMock(return_value={"id": "native-should-not-run"})
    if quote_raises:
        client.get_option_quote = AsyncMock(side_effect=RuntimeError("quote endpoint down"))
    else:
        client.get_option_quote = AsyncMock(return_value=quote)
    return client


def _exit_signal() -> SimpleNamespace:
    return SimpleNamespace(
        urgency="IMMEDIATE",
        reason="stop loss hit",
        rule_id="stop_loss_v1",
        confidence=1.0,
        details={"bucket": "SWING", "pnl_pct": -41.0},
    )


async def _close(engine: ExecutionEngine, client: AsyncMock, mark: float | None = 1.08) -> bool:
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client
    return await engine.close_position(
        ticker=OPTION, qty=10.0, exit_signal=_exit_signal(), direction="LONG", current_price=mark
    )


@pytest.mark.asyncio
async def test_exit_quote_captured_from_the_quote_that_priced_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    persist = AsyncMock()
    monkeypatch.setattr(ee_mod, "persist_exit_decision", persist)
    engine = _make_engine()
    client = _make_client(quote={"bid": 0.94, "ask": 1.06})

    assert await _close(engine, client, mark=1.08) is True

    quote = persist.await_args.kwargs["exit_quote"]
    assert quote["bid"] == 0.94
    assert quote["ask"] == 1.06
    assert quote["mid"] == pytest.approx(1.00)
    assert quote["spread_pct"] == pytest.approx(0.12)
    assert quote["mark_used_by_rule"] == 1.08
    assert quote["source"] == "gateway_option_quote"
    assert isinstance(quote["decision_ts"], str)

    # Reuse, not a second fetch: the chain call is the expensive part of the
    # close path and the exit path must never add latency before the submit.
    assert client.get_option_quote.await_count == 1


@pytest.mark.asyncio
async def test_close_is_still_submitted_when_the_quote_fetch_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A quote failure logs and proceeds — it may never block a close."""
    persist = AsyncMock()
    monkeypatch.setattr(ee_mod, "persist_exit_decision", persist)
    engine = _make_engine()
    client = _make_client(quote_raises=True)

    assert await _close(engine, client, mark=1.08) is True

    client.create_order.assert_awaited_once()
    client.close_position.assert_not_awaited()  # no native escalation
    quote = persist.await_args.kwargs["exit_quote"]
    assert quote["source"] == "tracked_mark"
    assert quote["mid"] is None
    assert quote["mark_used_by_rule"] == 1.08


@pytest.mark.asyncio
async def test_close_is_still_submitted_when_building_the_exit_quote_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a bug in the capture itself must not cost us the close."""
    persist = AsyncMock()
    monkeypatch.setattr(ee_mod, "persist_exit_decision", persist)
    monkeypatch.setattr(ee_mod, "build_exit_quote", Mock(side_effect=ValueError("boom")))
    engine = _make_engine()
    client = _make_client(quote={"bid": 0.94, "ask": 1.06})

    assert await _close(engine, client, mark=1.08) is True

    client.create_order.assert_awaited_once()
    assert persist.await_args.kwargs["exit_quote"] == {"error": "boom"}


@pytest.mark.asyncio
async def test_native_escalation_also_records_the_exit_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    """An escalated close is the EXPENSIVE one — the attributed limit was
    rejected. Omitting it would bias the measured sample toward cheap exits, so
    the escalation carries the same quote (its fill is not orion-attributed, so
    the aggregation reports it as unmeasured rather than measuring it)."""
    persist = AsyncMock()
    monkeypatch.setattr(ee_mod, "persist_exit_decision", persist)
    engine = _make_engine()
    client = _make_client(quote={"bid": 0.94, "ask": 1.06})
    client.create_order = AsyncMock(return_value={"error": "422", "detail": '{"code":42210000}', "status_code": 422})
    client.close_position = AsyncMock(return_value={"id": "native-1"})

    assert await _close(engine, client, mark=1.08) is True

    client.close_position.assert_awaited_once()
    quote = persist.await_args.kwargs["exit_quote"]
    assert quote["mid"] == pytest.approx(1.00)
    assert quote["source"] == "gateway_option_quote"


@pytest.mark.asyncio
async def test_decision_ts_is_the_quote_time_not_the_broker_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`decision_ts` must date the quote the close was priced from. Stamping it
    after the broker replies would make stale-quote analysis read a slow
    submission as a fresh quote."""
    persist = AsyncMock()
    monkeypatch.setattr(ee_mod, "persist_exit_decision", persist)
    engine = _make_engine()
    client = _make_client(quote={"bid": 0.94, "ask": 1.06})
    submitted_at: list[datetime] = []

    async def slow_submit(**_kwargs: object) -> dict[str, str]:
        await asyncio.sleep(0.05)
        submitted_at.append(datetime.now(UTC))
        return {"id": "broker-close-1", "status": "accepted"}

    client.create_order = AsyncMock(side_effect=slow_submit)

    assert await _close(engine, client, mark=1.08) is True

    decision_ts = datetime.fromisoformat(persist.await_args.kwargs["exit_quote"]["decision_ts"])
    assert decision_ts < submitted_at[0]


@pytest.mark.asyncio
async def test_quote_capture_does_not_change_the_limit_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """Routing/pricing is untouched: SELL-to-close still floors to the live bid."""
    monkeypatch.setattr(ee_mod, "persist_exit_decision", AsyncMock())
    engine = _make_engine()
    client = _make_client(quote={"bid": 0.94, "ask": 1.06})

    assert await _close(engine, client, mark=1.08) is True

    assert client.create_order.await_args.kwargs["limit_price"] == pytest.approx(0.90)
