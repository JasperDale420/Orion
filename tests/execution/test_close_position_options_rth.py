"""Tests for the options-aware ExecutionEngine.close_position.

Exit-pipeline RCA 2026-06-05 + adversarial review (Modes 2/3/4).

The options close path, inside RTH:
  1. Cancels Orion's own resting orders on the symbol (shared-account
     wash-trade / re-open hygiene). If a cancel can't be CONFIRMED, the close
     is deferred (returns False) rather than risking a wash/re-open.
  2. Submits an orion-attributed marketable LIMIT (``create_order``) as the
     PRIMARY close — keeping the fill orion-attributed so it feeds
     RiskManager.process_fill (PnL / daily-loss / kill-switch). The limit is a
     plain marketable price (mark*(1±0.075)), NOT floored against avg entry (an
     entry floor would make a loser's exit non-marketable and rest unfilled).
     It carries reduce-only position_intent; a 42210000 mismatch is NOT retried
     as a plain limit (that has a reduce-only race) — it escalates.
  3. If the limit is rejected (42210000, residual wash, anything) or can't be
     priced, it ESCALATES to the Gateway native ``DELETE /positions`` flatten
     (``close_position``) — reduce-only by construction and 404-safe. That fill
     is NOT orion-attributed, so it's the escape hatch, not the default.

Outside RTH the options path logs + returns False without submitting.
Equity behavior is unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from orion.execution.execution_engine import ExecutionEngine


def _make_engine(market_schedule_returns_options_open: bool = True) -> ExecutionEngine:
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
    engine._market_schedule.is_market_open_for_options = Mock(return_value=market_schedule_returns_options_open)
    engine._clock_closed_until = None
    engine._calendar_alert_ts = float("-inf")
    return engine


def _dead_calendar_engine() -> ExecutionEngine:
    """Engine whose calendar failed to load — the strict gate raises forever."""
    engine = _make_engine()
    engine._market_schedule.is_market_open_for_options = Mock(
        side_effect=RuntimeError("Cannot verify market hours without calendar")
    )
    return engine


def _make_client(
    *,
    broker_qty: str,
    avg_entry: str = "1.0",
    limit_result: dict | None = None,
    native_result: dict | None = None,
    open_orders: list | None = None,
    cancel_result: dict | None = None,
) -> AsyncMock:
    """Wire a gateway client mock for the close path.

    ``limit_result`` is what ``create_order`` returns (the primary limit close);
    ``native_result`` is what ``close_position`` returns (the escalation).
    """
    client = AsyncMock()
    client.get_position = AsyncMock(return_value={"qty": broker_qty, "avg_entry_price": avg_entry})
    client.get_orders = AsyncMock(return_value=open_orders if open_orders is not None else [])
    client.cancel_order = AsyncMock(return_value=cancel_result if cancel_result is not None else {"status": "canceled"})
    if limit_result is not None:
        client.create_order = AsyncMock(return_value=limit_result)
    if native_result is not None:
        client.close_position = AsyncMock(return_value=native_result)
    return client


def _exit_signal(reason: str = "profit target hit") -> SimpleNamespace:
    return SimpleNamespace(
        urgency="IMMEDIATE", reason=reason, rule_id="profit_target_v1", confidence=1.0, details={"bucket": "SWING"}
    )


def _patch_persist(monkeypatch: pytest.MonkeyPatch) -> None:
    import orion.execution.execution_engine as ee_mod

    monkeypatch.setattr(ee_mod, "persist_exit_decision", AsyncMock())
    monkeypatch.setattr(ee_mod, "persist_exit_order_rejection", AsyncMock())


@pytest.mark.asyncio
async def test_options_close_outside_rth_skips_submit() -> None:
    """When options market is closed, close_position must NOT submit any order
    (limit or native) and must not cancel."""
    engine = _make_engine(market_schedule_returns_options_open=False)
    client = _make_client(broker_qty="10", limit_result={"id": "x"}, native_result={"id": "y"})
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="NVDA260522C00250000", qty=10.0, exit_signal=_exit_signal(), direction="LONG", current_price=2.50
    )

    assert closed is False
    client.create_order.assert_not_called()
    client.close_position.assert_not_called()


@pytest.mark.asyncio
async def test_options_close_attempted_when_calendar_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead exchange calendar must not block exits.

    The strict gate raises when the calendar failed to load, and MarketSchedule
    is a process-lifetime singleton — so one failed init meant close_position
    could never submit ANY option exit for the life of the process, and an ITM
    contract rode to expiry to be auto-exercised into equity Orion can neither
    see nor sell (2a21775e). With the broker clock reporting open, the close
    must still be ATTEMPTED — and still reduce-only.
    """
    _patch_persist(monkeypatch)
    engine = _dead_calendar_engine()
    client = _make_client(broker_qty="10", avg_entry="1.0", limit_result={"id": "o", "status": "accepted"})
    client.get_clock = AsyncMock(return_value={"is_open": True})
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="NVDA260522C00250000", qty=10.0, exit_signal=_exit_signal(), direction="LONG", current_price=2.50
    )

    assert closed is True
    client.create_order.assert_called_once()
    assert client.create_order.call_args[1]["position_intent"] == "sell_to_close"


@pytest.mark.asyncio
async def test_calendar_fallback_trusts_the_broker_over_the_wall_clock() -> None:
    """The half-day trap (Codex review): a weekday 9:30–16:00 wall clock would
    call 14:00 ET open on a session that closed at 13:00. Alpaca QUEUES a day
    order placed after the close instead of rejecting it, and the close path
    records an `accepted` order as a completed exit — so the 0DTE would be
    dropped from tracking and still expire unexited. The broker clock knows the
    session ended; nothing may be submitted."""
    engine = _dead_calendar_engine()
    client = _make_client(broker_qty="10", limit_result={"id": "x"}, native_result={"id": "y"})
    client.get_clock = AsyncMock(return_value={"is_open": False})
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="NVDA260522C00250000", qty=10.0, exit_signal=_exit_signal(), direction="LONG", current_price=2.50
    )

    assert closed is False
    client.create_order.assert_not_called()
    client.close_position.assert_not_called()


@pytest.mark.parametrize(
    "clock",
    [
        {"error": "Server error '500'"},
        {},  # no is_open field
        {"is_open": "true"},  # truthy but not the boolean
        ["not", "a", "dict"],  # malformed payload
    ],
)
@pytest.mark.asyncio
async def test_calendar_fallback_fails_closed_on_any_unusable_clock(clock) -> None:
    """Only an unambiguous `is_open: True` from a well-formed payload may
    authorize a submit. A blind submit is worse than a delayed exit."""
    engine = _dead_calendar_engine()
    client = _make_client(broker_qty="10", limit_result={"id": "x"}, native_result={"id": "y"})
    client.get_clock = AsyncMock(return_value=clock)
    engine._get_gateway_client = lambda: client

    assert await engine.options_close_window_open() is False


@pytest.mark.asyncio
async def test_calendar_fallback_fails_closed_when_clock_raises() -> None:
    engine = _dead_calendar_engine()
    client = _make_client(broker_qty="10")
    client.get_clock = AsyncMock(side_effect=RuntimeError("gateway down"))
    engine._get_gateway_client = lambda: client

    assert await engine.options_close_window_open() is False


@pytest.mark.asyncio
async def test_calendar_fallback_caches_the_closed_verdict() -> None:
    """Closed is the state the every-~5s retry across every open position sits
    in, so caching it is what keeps this off a Gateway that has answered a 429
    storm before. A stale `closed` only ever delays an exit."""
    engine = _dead_calendar_engine()
    client = _make_client(broker_qty="10")
    client.get_clock = AsyncMock(return_value={"is_open": False})
    engine._get_gateway_client = lambda: client

    assert await engine.options_close_window_open() is False
    assert await engine.options_close_window_open() is False
    client.get_clock.assert_awaited_once()


@pytest.mark.asyncio
async def test_open_verdict_is_reused_only_by_the_advisory_gates() -> None:
    """An `open` verdict is reusable for a moment, so the monitor's pre-check and
    the engine's entry gate — milliseconds apart on the same attempt — don't each
    re-read the clock. The immediately-before-submit check must never reuse it: a
    stale `open` authorizes a submit after the session close, which Alpaca queues
    rather than rejects (Codex review)."""
    engine = _dead_calendar_engine()
    client = _make_client(broker_qty="10")
    client.get_clock = AsyncMock(return_value={"is_open": True})
    engine._get_gateway_client = lambda: client

    assert await engine.options_close_window_open() is True
    assert await engine.options_close_window_open() is True
    client.get_clock.assert_awaited_once()  # advisory → reused

    client.get_clock.return_value = {"is_open": False}
    assert await engine.options_close_window_open(require_fresh=True) is False  # always re-reads


@pytest.mark.asyncio
async def test_options_close_aborted_when_session_closes_mid_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate is separated from the submit by a cancel sweep and a fresh quote.
    If the session closes across those awaits, nothing may be submitted — a
    queued day order would be recorded as a completed exit and the still-open
    position dropped from tracking (Codex review)."""
    _patch_persist(monkeypatch)
    engine = _dead_calendar_engine()
    client = _make_client(broker_qty="10", avg_entry="1.0", limit_result={"id": "o"}, native_result={"id": "n"})
    # Open at the gate, closed by the time the submit is reached.
    client.get_clock = AsyncMock(side_effect=[{"is_open": True}, {"is_open": False}])
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="NVDA260522C00250000", qty=10.0, exit_signal=_exit_signal(), direction="LONG", current_price=2.50
    )

    assert closed is False
    client.create_order.assert_not_called()
    client.close_position.assert_not_called()


@pytest.mark.asyncio
async def test_native_escalation_aborted_when_session_closes_mid_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same guard on the escalation path: it is reached only after a quote
    lookup (and sometimes a position re-check), so it can cross the close too."""
    _patch_persist(monkeypatch)
    engine = _dead_calendar_engine()
    # No mark and no fresh quote → straight to the native escalation.
    client = _make_client(broker_qty="10", native_result={"id": "should-not-be-used"})
    client.get_clock = AsyncMock(side_effect=[{"is_open": True}, {"is_open": False}])
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="NVDA260522C00250000", qty=10.0, exit_signal=_exit_signal(), direction="LONG", current_price=None
    )

    assert closed is False
    client.close_position.assert_not_called()


@pytest.mark.asyncio
async def test_options_close_uses_attributed_limit_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """Primary close is the orion-attributed LIMIT (create_order), NOT the
    native flatten. On success no native close is submitted."""
    _patch_persist(monkeypatch)
    engine = _make_engine(market_schedule_returns_options_open=True)
    client = _make_client(
        broker_qty="10",
        avg_entry="1.0",
        limit_result={"id": "order-opt", "status": "accepted"},
        native_result={"id": "should-not-be-used"},
    )
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="NVDA260522C00250000", qty=10.0, exit_signal=_exit_signal(), direction="LONG", current_price=2.50
    )

    assert closed is True
    client.create_order.assert_called_once()
    kwargs = client.create_order.call_args[1]
    assert kwargs["order_type"] == "limit"
    assert kwargs["side"] == "sell"  # LONG close → SELL
    assert kwargs["position_intent"] == "sell_to_close"
    client.close_position.assert_not_called()


@pytest.mark.asyncio
async def test_options_limit_is_marketable_not_floored(monkeypatch: pytest.MonkeyPatch) -> None:
    """The close limit is marketable (mark-based), NOT floored against avg
    entry. Flooring a loser's exit above entry would make it non-marketable and
    rest unfilled while we report success (adversarial review). A loser (mark
    below entry) must price BELOW entry so it actually fills; residual self-wash
    is handled by cancel-resting + native escalation, not an entry floor."""
    _patch_persist(monkeypatch)
    engine = _make_engine(market_schedule_returns_options_open=True)
    client = _make_client(broker_qty="1", avg_entry="5.0", limit_result={"id": "o", "status": "accepted"})
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="MU260612P00790000", qty=1.0, exit_signal=_exit_signal(), direction="LONG", current_price=2.0
    )

    assert closed is True
    limit = client.create_order.call_args[1]["limit_price"]
    assert limit < 5.0, f"limit {limit} must be marketable (below the $5 entry for a loser), not floored above it"
    assert 1.7 <= limit <= 2.0, f"limit {limit} should be ~mark*0.925 for a $2.0 mark"


@pytest.mark.asyncio
async def test_options_limit_side_from_qty_sign_not_direction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Broker qty sign is ground truth: broker holds LONG (qty=+10) but caller
    says direction='SHORT' (stale) → side MUST be SELL, never BUY."""
    _patch_persist(monkeypatch)
    engine = _make_engine(market_schedule_returns_options_open=True)
    client = _make_client(broker_qty="10", avg_entry="1.0", limit_result={"id": "o", "status": "accepted"})
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="NVDA260522C00250000", qty=10.0, exit_signal=_exit_signal("x"), direction="SHORT", current_price=2.50
    )

    assert closed is True
    assert client.create_order.call_args[1]["side"] == "sell"


@pytest.mark.asyncio
async def test_options_short_cover_buys_above_mark(monkeypatch: pytest.MonkeyPatch) -> None:
    """A SHORT options close (broker qty < 0) BUYs-to-cover, lifting the offer
    above the mark to fill. Side derives from the qty sign, not direction."""
    _patch_persist(monkeypatch)
    engine = _make_engine(market_schedule_returns_options_open=True)
    client = _make_client(broker_qty="-5", avg_entry="5.0", limit_result={"id": "o", "status": "accepted"})
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="SPY260522P00450000",
        qty=-5.0,
        exit_signal=_exit_signal("time to expiry"),
        direction="SHORT",
        current_price=4.00,
    )

    assert closed is True
    kwargs = client.create_order.call_args[1]
    assert kwargs["side"] == "buy"
    assert kwargs["qty"] == 5.0
    assert kwargs["limit_price"] > 4.00  # BUY-to-cover lifts the offer above mark


@pytest.mark.asyncio
async def test_options_42210000_escalates_to_native_no_plain_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mode 3: the limit sets position_intent=sell_to_close. A 42210000 mismatch
    is NOT retried as a plain (no-intent) limit — that retry has an inherent
    reduce-only race (the live position can change between any pre-check and the
    order). Instead it escalates to the native flatten, which is reduce-only by
    construction and 404-safe (adversarial review)."""
    _patch_persist(monkeypatch)
    engine = _make_engine(market_schedule_returns_options_open=True)
    client = _make_client(
        broker_qty="10",
        avg_entry="1.0",
        limit_result={
            "error": "Client error '403'",
            "detail": '{"code":42210000,"message":"intent mismatch"}',
            "status_code": 403,
        },
        native_result={"id": "native-ok", "status": "accepted"},
    )
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="MSTR260612P00120000", qty=1.0, exit_signal=_exit_signal(), direction="LONG", current_price=7.00
    )

    assert closed is True
    # Exactly ONE limit attempt (with intent) — no plain no-intent retry.
    client.create_order.assert_called_once()
    assert client.create_order.call_args[1].get("position_intent") == "sell_to_close"
    # Escalated to the native flatten.
    client.close_position.assert_called_once_with("MSTR260612P00120000", qty=1.0)


@pytest.mark.asyncio
async def test_options_close_stops_when_position_vanished_on_reject(monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-07-01 loop: a sell_to_close limit is rejected 422 'position intent
    mismatch, inferred: sell_to_open' AND the position has vanished (Alpaca 404,
    already closed/expired). Re-verify shows the broker holds no position, so the
    close already succeeded — record success and STOP. Must NOT escalate to the
    native flatten: DELETE /positions re-submits a closing order into the same
    wall, which looped the reject 4× per contract."""
    _patch_persist(monkeypatch)
    engine = _make_engine(market_schedule_returns_options_open=True)
    client = _make_client(
        broker_qty="10",
        avg_entry="1.0",
        limit_result={
            "error": "Client error '422'",
            "detail": '{"code":42210000,"message":"position intent mismatch, inferred: sell_to_open, specified: sell_to_close"}',
            "status_code": 422,
        },
        native_result={"id": "should-not-be-used"},
    )
    # Present at the pre-check, GONE (404) at the post-reject re-verify.
    client.get_position = AsyncMock(
        side_effect=[
            {"qty": "10", "avg_entry_price": "1.0"},
            {"error": "Client error '404'", "detail": "position not found", "status_code": 404},
        ]
    )
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="COIN260703C00300000", qty=10.0, exit_signal=_exit_signal(), direction="LONG", current_price=2.50
    )

    assert closed is True  # already closed → done, stop retrying
    client.create_order.assert_called_once()  # exactly one limit attempt
    client.close_position.assert_not_called()  # NO native flatten (it would loop)


@pytest.mark.asyncio
async def test_options_close_escalates_when_recheck_errors_transiently(monkeypatch: pytest.MonkeyPatch) -> None:
    """A TRANSIENT position re-check error after the limit reject (500/429/auth —
    NOT a 404) must NOT be read as 'position gone'. GatewayTradingClient renders
    every HTTP error as {"error": ...}, so a bare error check would silently mark
    a still-open option closed (Codex review P1). Such a recheck must fall
    through to the native flatten, never be recorded as a completed close."""
    _patch_persist(monkeypatch)
    engine = _make_engine(market_schedule_returns_options_open=True)
    client = _make_client(
        broker_qty="10",
        avg_entry="1.0",
        limit_result={
            "error": "Client error '422'",
            "detail": '{"code":42210000,"message":"position intent mismatch, inferred: sell_to_open"}',
            "status_code": 422,
        },
        native_result={"id": "native-ok", "status": "accepted"},
    )
    # Present at pre-check; a TRANSIENT 500 (not a 404) at the post-reject recheck.
    client.get_position = AsyncMock(
        side_effect=[
            {"qty": "10", "avg_entry_price": "1.0"},
            {"error": "Server error '500'", "detail": "gateway down", "status_code": 500},
        ]
    )
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="PLTR260703C00030000", qty=10.0, exit_signal=_exit_signal(), direction="LONG", current_price=2.50
    )

    assert closed is True  # native flatten succeeded
    # Escalated to the native flatten — NOT silently recorded as closed.
    client.close_position.assert_called_once_with("PLTR260703C00030000", qty=10.0)


@pytest.mark.asyncio
async def test_options_limit_rejected_escalates_to_native(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the attributed limit is rejected (e.g. a residual wash), escalate to
    the native flatten to avoid stranding the position."""
    _patch_persist(monkeypatch)
    engine = _make_engine(market_schedule_returns_options_open=True)
    client = _make_client(
        broker_qty="2",
        avg_entry="5.0",
        limit_result={"error": "403", "detail": "potential wash trade detected", "status_code": 403},
        native_result={"id": "native-ok", "status": "accepted"},
    )
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="MU260612P00790000", qty=2.0, exit_signal=_exit_signal(), direction="LONG", current_price=10.0
    )

    assert closed is True
    client.create_order.assert_called_once()
    client.close_position.assert_called_once_with("MU260612P00790000", qty=2.0)


@pytest.mark.asyncio
async def test_options_ambiguous_limit_outcome_defers_no_escalation(monkeypatch: pytest.MonkeyPatch) -> None:
    """An AMBIGUOUS limit outcome (timeout / transport error → {"error"} with NO
    status_code) must NOT escalate to the native flatten: the limit may have been
    accepted and be resting, so a native close now could double-close and re-open
    the naked-short hole. Defer (return False) instead — next cycle cancels any
    live limit first (adversarial review)."""
    _patch_persist(monkeypatch)
    engine = _make_engine(market_schedule_returns_options_open=True)
    client = _make_client(
        broker_qty="2",
        avg_entry="5.0",
        limit_result={"error": "ReadTimeout: gateway did not respond"},  # no status_code → ambiguous
        native_result={"id": "should-not-be-used"},
    )
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="MU260612P00790000", qty=2.0, exit_signal=_exit_signal(), direction="LONG", current_price=10.0
    )

    assert closed is False  # deferred, not escalated
    client.create_order.assert_called_once()
    client.close_position.assert_not_called()  # NO native flatten while the limit may be live


@pytest.mark.asyncio
async def test_options_no_mark_escalates_to_native(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a mark we can't price an attributed limit → escalate straight to
    the native flatten (no blind limit)."""
    _patch_persist(monkeypatch)
    engine = _make_engine(market_schedule_returns_options_open=True)
    client = _make_client(broker_qty="10", native_result={"id": "native-ok", "status": "accepted"})
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="NVDA260522C00250000", qty=10.0, exit_signal=_exit_signal(), direction="LONG", current_price=None
    )

    assert closed is True
    client.create_order.assert_not_called()
    client.close_position.assert_called_once_with("NVDA260522C00250000", qty=10.0)


@pytest.mark.asyncio
async def test_options_close_defers_when_cancel_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """If cancelling our own resting order is REJECTED (Gateway returns
    {"error": ...}, not an exception), the close is deferred (returns False) and
    submits nothing — a still-live resting buy could otherwise re-open exposure
    or self-wash the close (adversarial review)."""
    _patch_persist(monkeypatch)
    engine = _make_engine(market_schedule_returns_options_open=True)
    client = _make_client(
        broker_qty="2",
        avg_entry="5.0",
        open_orders=[{"id": "ord-orion", "symbol": "MU260612P00790000", "client_order_id": "orion_resting"}],
        cancel_result={"error": "Client error '403'", "detail": "cannot cancel"},
        limit_result={"id": "should-not-be-used"},
        native_result={"id": "should-not-be-used"},
    )
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="MU260612P00790000", qty=2.0, exit_signal=_exit_signal(), direction="LONG", current_price=10.0
    )

    assert closed is False
    client.create_order.assert_not_called()
    client.close_position.assert_not_called()


@pytest.mark.asyncio
async def test_options_cancels_only_orion_resting_orders(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before closing, cancel Orion's OWN resting orders on the symbol (orion_
    prefix) — never a sibling's, never a different symbol — and clear them from
    risk pending exposure on success."""
    _patch_persist(monkeypatch)
    engine = _make_engine(market_schedule_returns_options_open=True)
    open_orders = [
        {"id": "ord-orion", "symbol": "MU260612P00790000", "client_order_id": "orion_resting_buy"},
        {"id": "ord-sibling", "symbol": "MU260612P00790000", "client_order_id": "cerberus_buy"},
        {"id": "ord-other-sym", "symbol": "NVDA260618C00230000", "client_order_id": "orion_other"},
    ]
    client = _make_client(
        broker_qty="2",
        avg_entry="1.0",
        open_orders=open_orders,
        limit_result={"id": "order-opt", "status": "accepted"},
    )
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="MU260612P00790000", qty=2.0, exit_signal=_exit_signal(), direction="LONG", current_price=10.0
    )

    assert closed is True
    client.cancel_order.assert_called_once_with("ord-orion")
    engine.risk_manager.remove_pending_order.assert_awaited_once_with("orion_resting_buy")


@pytest.mark.asyncio
async def test_close_failure_persists_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    """When BOTH the attributed limit and the native escalation fail, a REJECTED
    orders row is persisted so the failure is auditable (RCA 2026-06-05)."""
    import orion.execution.execution_engine as ee_mod

    monkeypatch.setattr(ee_mod, "persist_exit_decision", AsyncMock())
    rejection = AsyncMock()
    monkeypatch.setattr(ee_mod, "persist_exit_order_rejection", rejection)

    engine = _make_engine(market_schedule_returns_options_open=True)
    client = _make_client(
        broker_qty="2",
        avg_entry="5.0",
        limit_result={"error": "403", "detail": "potential wash trade detected", "status_code": 403},
        native_result={"error": "500", "detail": "gateway down"},
    )
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="MU260612P00790000", qty=2.0, exit_signal=_exit_signal(), direction="LONG", current_price=10.0
    )

    assert closed is False
    rejection.assert_awaited_once()
    kwargs = rejection.await_args.kwargs
    assert kwargs["ticker"] == "MU260612P00790000"
    assert kwargs["side"] == "sell"
    assert kwargs["qty"] == 2.0


@pytest.mark.asyncio
async def test_equity_close_immediate_still_uses_market(monkeypatch: pytest.MonkeyPatch) -> None:
    """Equity behavior unchanged: IMMEDIATE urgency goes via the market-order
    close_position path. Only options get the limit-first treatment."""
    _patch_persist(monkeypatch)
    engine = _make_engine(market_schedule_returns_options_open=False)  # off-hours
    client = _make_client(broker_qty="10", native_result={"id": "order-eq", "status": "accepted"})
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="AAPL", qty=10.0, exit_signal=_exit_signal("x"), direction="LONG", current_price=180.0
    )

    assert closed is True
    client.close_position.assert_called_once_with("AAPL", qty=10.0)
    client.create_order.assert_not_called()


@pytest.mark.asyncio
async def test_options_close_prices_off_fresh_bid_not_stale_mark(monkeypatch: pytest.MonkeyPatch) -> None:
    """RCA 2026-06-08: the options close must price off a FRESH chain quote (the
    live bid for a SELL-to-close), NOT a stale tracked mark. A stale mark (21.0
    for a contract worth ~6.45) produced an off-market resting order that
    reserved the long for ~30 min and got every new close rejected 40310000.
    With a fresh bid of 6.40, the limit is 6.40 even though current_price=21.0."""
    _patch_persist(monkeypatch)
    engine = _make_engine(market_schedule_returns_options_open=True)
    client = _make_client(broker_qty="2", avg_entry="5.0", limit_result={"id": "o", "status": "accepted"})
    client.get_option_quote = AsyncMock(return_value={"bid": 6.40, "ask": 6.60, "last": 6.45})
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="MU260612P00790000",
        qty=2.0,
        exit_signal=_exit_signal(),
        direction="LONG",
        current_price=21.0,  # deliberately stale — must be ignored in favour of the fresh bid
    )

    assert closed is True
    limit = client.create_order.call_args[1]["limit_price"]
    assert limit == pytest.approx(6.40), f"expected marketable fresh bid 6.40, got {limit} (stale-mark bug?)"


@pytest.mark.asyncio
async def test_options_close_falls_back_to_mark_without_fresh_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    """No fresh chain quote (empty / biddless) → fall back to the tracked-mark
    pricing (mark*0.925 for a sell). Preserves behaviour when the chain is down."""
    _patch_persist(monkeypatch)
    engine = _make_engine(market_schedule_returns_options_open=True)
    client = _make_client(broker_qty="2", avg_entry="5.0", limit_result={"id": "o", "status": "accepted"})
    client.get_option_quote = AsyncMock(return_value={})
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="MU260612P00790000", qty=2.0, exit_signal=_exit_signal(), direction="LONG", current_price=2.0
    )

    assert closed is True
    limit = client.create_order.call_args[1]["limit_price"]
    assert 1.7 <= limit <= 2.0, f"expected mark*0.925 fallback ~1.85, got {limit}"


@pytest.mark.asyncio
async def test_options_short_cover_prices_off_fresh_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    """A SHORT cover (broker qty<0 → BUY-to-cover) lifts the live ask, ceil-rounded."""
    _patch_persist(monkeypatch)
    engine = _make_engine(market_schedule_returns_options_open=True)
    client = _make_client(broker_qty="-5", avg_entry="5.0", limit_result={"id": "o", "status": "accepted"})
    client.get_option_quote = AsyncMock(return_value={"bid": 4.00, "ask": 4.20})
    engine._gateway_client = client
    engine._get_gateway_client = lambda: client

    closed = await engine.close_position(
        ticker="SPY260522P00450000", qty=-5.0, exit_signal=_exit_signal(), direction="SHORT", current_price=4.0
    )

    assert closed is True
    kwargs = client.create_order.call_args[1]
    assert kwargs["side"] == "buy"
    assert kwargs["limit_price"] == pytest.approx(4.20)
