"""Stale unfilled ENTRY orders must be cancelled at the broker, not left to
expire at the close.

2026-06-09: Orion's mid-priced DAY limit entries (EWY x7 @14:02, EWY x1 @15:45,
XHB x1 @16:42) sat unfilled all session — reserving shared day-trading buying
power and risking a late fill on a stale signal — then expired at the close.
`poll_fills` updated their status but never cancelled them.

The `orders` table holds ENTRIES only (closes persist to exit_decisions and
bracket SL/TP legs are never persisted there), so querying it scopes the
auto-cancel sweep to genuine buy-to-open entries — a buy-to-close on a short
position can never be cancelled by this path.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///:memory:")

from sqlalchemy import select

from orion.execution.execution_engine import ExecutionEngine
from orion.storage.db import async_session_factory, init_db
from orion.storage.models_execution import OrderRecord


def _engine() -> ExecutionEngine:
    """Bare engine; the DB query is stubbed so these target cancel logic only."""
    ee = ExecutionEngine.__new__(ExecutionEngine)
    ee._remove_pending_order_compat = AsyncMock()
    # __new__ bypasses __init__, so the cancel-state dict must be seeded here
    # (the engine lazily seeds it too, but tests assert on it directly).
    ee._cancel_attempts = {}
    return ee


# ── cancel logic (DB query stubbed) ──────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancels_each_stale_entry_and_drops_pending() -> None:
    ee = _engine()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[
            {"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "EWY"},
            {"broker_order_id": "b-2", "client_order_id": "orion_b", "ticker": "XHB"},
        ]
    )
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value={})  # success

    n = await ee._cancel_stale_entry_orders(client)

    assert n == 2
    assert client.cancel_order.await_count == 2
    client.cancel_order.assert_any_await("b-1")
    client.cancel_order.assert_any_await("b-2")
    assert ee._remove_pending_order_compat.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_rejection_is_not_counted_and_keeps_pending() -> None:
    """Gateway surfaces failures as {"error": ...} (not exceptions). A rejected
    cancel must not be counted and must not drop the pending-order reservation."""
    ee = _engine()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "EWY"}]
    )
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value={"error": "order not cancelable"})

    n = await ee._cancel_stale_entry_orders(client)

    assert n == 0
    ee._remove_pending_order_compat.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_exception_is_swallowed() -> None:
    ee = _engine()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "EWY"}]
    )
    client = AsyncMock()
    client.cancel_order = AsyncMock(side_effect=RuntimeError("gateway down"))

    n = await ee._cancel_stale_entry_orders(client)  # must not raise

    assert n == 0
    ee._remove_pending_order_compat.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_stale_orders_is_noop() -> None:
    ee = _engine()
    ee._fetch_stale_entry_orders = AsyncMock(return_value=[])
    client = AsyncMock()

    n = await ee._cancel_stale_entry_orders(client)

    assert n == 0
    client.cancel_order.assert_not_awaited()


# ── 429 storm prevention: per-order backoff + give-up ─────────────────────────


def _patch_clock(monkeypatch, start: float = 1000.0) -> list[float]:
    """Make `time.monotonic()` return a controllable value. Returns a 1-element
    list whose [0] entry is the current clock; mutate it to advance time."""
    import orion.execution.execution_engine as mod

    clock = [start]
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock[0])
    # Deterministic jitter so backoff windows are exact in tests.
    monkeypatch.setattr(mod, "_cancel_backoff_jitter", lambda: 0.0)
    return clock


@pytest.mark.unit
@pytest.mark.asyncio
async def test_429_reject_is_not_reattempted_next_sweep(monkeypatch) -> None:
    """A transient 429 reject must set a backoff so the SAME order is skipped on
    the immediately-following sweep (this is the 5s storm we are killing)."""
    clock = _patch_clock(monkeypatch)
    ee = _engine()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "EWY"}]
    )
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value={"error": "rate limited", "status_code": 429})

    # Sweep 1: attempts the cancel, gets a 429, arms backoff.
    n1 = await ee._cancel_stale_entry_orders(client)
    assert n1 == 0
    assert client.cancel_order.await_count == 1

    # Sweep 2 (immediately after, clock barely advanced): order is in backoff,
    # so NO new cancel call is made — the storm is broken.
    clock[0] += 1.0
    n2 = await ee._cancel_stale_entry_orders(client)
    assert n2 == 0
    assert client.cancel_order.await_count == 1  # unchanged — backoff respected

    # After the backoff window elapses, it is eligible again (still transient).
    clock[0] += 10_000.0
    n3 = await ee._cancel_stale_entry_orders(client)
    assert n3 == 0
    assert client.cancel_order.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_permanent_rejection_gives_up_after_one_attempt(monkeypatch) -> None:
    """A permanent rejection (GW-E2009 / trading capability required) must give
    up after ONE attempt and never be retried, even far in the future."""
    _patch_clock(monkeypatch)
    sent: list[str] = []

    import orion.execution.execution_engine as mod

    async def _fake_alert(message, *, dedupe_key=None):
        sent.append(dedupe_key or message)
        return True

    monkeypatch.setattr(mod, "send_discord_alert", _fake_alert, raising=False)

    ee = _engine()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "EWY"}]
    )
    client = AsyncMock()
    client.cancel_order = AsyncMock(
        return_value={
            "error": "Client error '403 Forbidden'",
            "detail": "GW-E2009 Trading capability required",
            "status_code": 403,
        }
    )

    n1 = await ee._cancel_stale_entry_orders(client)
    assert n1 == 0
    assert client.cancel_order.await_count == 1
    assert ee._cancel_attempts["b-1"].gave_up is True
    assert len(sent) == 0  # one Gateway permission problem, not one page per stale order

    # No matter how much time passes, a gave_up order is never retried again.
    import orion.execution.execution_engine as mod2

    mod2.time.monotonic = lambda: 10**9
    n2 = await ee._cancel_stale_entry_orders(client)
    assert n2 == 0
    assert client.cancel_order.await_count == 1  # never retried
    assert len(sent) == 0  # no alert storm on restart


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transient_gives_up_after_max_attempts(monkeypatch) -> None:
    """Repeated transient 429s eventually give up (after MAX_ATTEMPTS) with one
    alert, so a permanently-uncancelable-but-429ing order can't loop forever."""
    clock = _patch_clock(monkeypatch)
    sent: list[str] = []

    import orion.execution.execution_engine as mod

    async def _fake_alert(message, *, dedupe_key=None):
        sent.append(dedupe_key or message)
        return True

    monkeypatch.setattr(mod, "send_discord_alert", _fake_alert, raising=False)

    ee = _engine()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "EWY"}]
    )
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value={"error": "rate limited", "status_code": 429})

    max_attempts = mod.ExecutionEngine._CANCEL_MAX_ATTEMPTS
    # Drive enough sweeps (advancing past each backoff) to exhaust attempts.
    for _ in range(max_attempts + 3):
        clock[0] += 10_000.0
        await ee._cancel_stale_entry_orders(client)

    assert client.cancel_order.await_count == max_attempts
    assert ee._cancel_attempts["b-1"].gave_up is True
    assert len(sent) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generic_4xx_is_transient_not_permanent(monkeypatch) -> None:
    """A generic non-429 4xx with NO known permanent marker (e.g. a 409) must be
    treated as TRANSIENT — backed off, NOT given up after a single attempt — so a
    retryable reject never strands the order's DTBP for the session."""
    clock = _patch_clock(monkeypatch)
    ee = _engine()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "EWY"}]
    )
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value={"error": "Client error '409 Conflict'", "status_code": 409})

    await ee._cancel_stale_entry_orders(client)
    state = ee._cancel_attempts["b-1"]
    assert state.gave_up is False  # NOT stranded after one attempt
    assert state.attempts == 1
    assert state.next_eligible > clock[0]  # backoff armed -> retryable later


@pytest.mark.unit
@pytest.mark.asyncio
async def test_giveup_alert_undelivered_still_records(monkeypatch) -> None:
    """If a transient give-up Discord page is not delivered (send returns False — no webhook /
    dedupe / failure), the order must still give up cleanly and not crash; the
    durable ERROR log is the operator's guaranteed signal."""
    clock = _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    async def _undelivered_alert(message, *, dedupe_key=None):
        return False  # e.g. no webhook configured

    monkeypatch.setattr(mod, "send_discord_alert", _undelivered_alert, raising=False)

    # Capture the module logger so we can assert the give-up is durably recorded
    # even when Discord delivery fails (the operator's guaranteed signal).
    error_events: list[str] = []
    warning_events: list[str] = []
    real_logger = mod.logger

    class _CapLogger:
        def error(self, event, *a, **kw):
            error_events.append(event)

        def warning(self, event, *a, **kw):
            warning_events.append(event)

        def __getattr__(self, name):
            return getattr(real_logger, name)

    monkeypatch.setattr(mod, "logger", _CapLogger(), raising=False)

    ee = _engine()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "EWY"}]
    )
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value={"error": "rate limited", "status_code": 429})

    for _ in range(mod.ExecutionEngine._CANCEL_MAX_ATTEMPTS):
        clock[0] += 10_000.0
        n = await ee._cancel_stale_entry_orders(client)  # must not raise
    assert n == 0
    assert ee._cancel_attempts["b-1"].gave_up is True
    assert ee._cancel_attempts["b-1"].alerted is True
    # Durable give-up signal guaranteed despite the failed Discord delivery.
    assert "stale_cancel_gave_up" in error_events
    assert "stale_cancel_giveup_alert_undelivered" in warning_events


@pytest.mark.unit
@pytest.mark.asyncio
async def test_backed_off_orders_do_not_consume_per_cycle_cap(monkeypatch) -> None:
    """An order inside its backoff window is skipped BEFORE the per-cycle cap is
    counted, so a backlog of backed-off orders can't starve still-eligible ones
    out of their cancel attempt this sweep."""
    clock = _patch_clock(monkeypatch)
    ee = _engine()
    # b-0 already gave up; b-1 is mid-backoff; b-2 is fresh and eligible.
    from orion.execution.execution_engine import _CancelState

    ee._cancel_attempts = {
        "b-0": _CancelState(gave_up=True),
        "b-1": _CancelState(attempts=1, next_eligible=clock[0] + 10_000.0),
    }
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[
            {"broker_order_id": "b-0", "client_order_id": "orion_0", "ticker": "EWY"},
            {"broker_order_id": "b-1", "client_order_id": "orion_1", "ticker": "EWY"},
            {"broker_order_id": "b-2", "client_order_id": "orion_2", "ticker": "EWY"},
        ]
    )
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value={})  # success

    n = await ee._cancel_stale_entry_orders(client)
    assert n == 1  # only the eligible b-2 was attempted
    client.cancel_order.assert_awaited_once_with("b-2")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_per_cycle_cap_bounds_attempts(monkeypatch) -> None:
    """A single sweep attempts at most _CANCEL_MAX_PER_CYCLE cancels even when
    many orders are stale — bounds Gateway load per sweep."""
    _patch_clock(monkeypatch)

    import orion.execution.execution_engine as mod

    cap = mod.ExecutionEngine._CANCEL_MAX_PER_CYCLE
    stale = [{"broker_order_id": f"b-{i}", "client_order_id": f"orion_{i}", "ticker": "EWY"} for i in range(cap + 10)]
    ee = _engine()
    ee._fetch_stale_entry_orders = AsyncMock(return_value=stale)
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value={})  # all succeed

    await ee._cancel_stale_entry_orders(client)
    assert client.cancel_order.await_count == cap


@pytest.mark.unit
@pytest.mark.asyncio
async def test_success_clears_state(monkeypatch) -> None:
    """A successful cancel after a prior reject clears the order's cancel state
    (and still drops the pending reservation)."""
    clock = _patch_clock(monkeypatch)
    ee = _engine()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "EWY"}]
    )
    client = AsyncMock()
    # First a 429 to seed state, then advance past backoff and succeed.
    client.cancel_order = AsyncMock(side_effect=[{"error": "rate limited", "status_code": 429}, {}])

    await ee._cancel_stale_entry_orders(client)
    assert "b-1" in ee._cancel_attempts

    clock[0] += 10_000.0
    n = await ee._cancel_stale_entry_orders(client)
    assert n == 1
    assert "b-1" not in ee._cancel_attempts  # state cleared on success
    ee._remove_pending_order_compat.assert_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_state_pruned_for_orders_no_longer_stale(monkeypatch) -> None:
    """Cancel-state for orders that drop out of the stale set is pruned so the
    dict can't grow unbounded over a long-running process."""
    clock = _patch_clock(monkeypatch)
    ee = _engine()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "EWY"}]
    )
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value={"error": "rate limited", "status_code": 429})

    await ee._cancel_stale_entry_orders(client)
    assert "b-1" in ee._cancel_attempts

    # b-1 no longer stale (filled / canceled elsewhere); only b-2 stale now.
    clock[0] += 10_000.0
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-2", "client_order_id": "orion_b", "ticker": "XHB"}]
    )
    await ee._cancel_stale_entry_orders(client)
    assert "b-1" not in ee._cancel_attempts  # pruned
    assert "b-2" in ee._cancel_attempts


# ── broker-state reconcile: cancel rejected because already terminal ──────────


def _gateway_already_terminal_reject(state: str) -> dict[str, object]:
    """The exact dict GatewayTradingClient returns when Alpaca rejects a cancel
    because the order is already terminal. `_request` sets `detail` to the RAW
    response body (`exc.response.text`), so Alpaca's message is DOUBLE-JSON-
    escaped — `\\"filled\\"`, not a clean quote. The parser must match that real
    production shape, so the tests build it the same way the gateway does."""
    alpaca_err = json.dumps({"code": 42210000, "message": f'order is already in "{state}" state'})
    body = json.dumps(
        {
            "success": False,
            "error": {"code": "GW-E8001", "message": f"Alpaca API Error: {alpaca_err}"},
            "detail": f"Alpaca API Error: {alpaca_err}",
        }
    )
    return {"error": "Client error '422 Unprocessable Entity'", "detail": body, "status_code": 422}


@pytest.mark.unit
def test_parse_already_terminal_state_handles_double_escaped_body() -> None:
    """The parser must see through the gateway's double-JSON-escaped body (the
    bug a naive single-quote regex would miss) and ignore non-terminal rejects."""
    from orion.execution.execution_engine import _parse_already_terminal_state

    assert _parse_already_terminal_state(_gateway_already_terminal_reject("filled")) == "filled"
    assert _parse_already_terminal_state(_gateway_already_terminal_reject("expired")) == "expired"
    # Alpaca's British 'cancelled' normalises to the OrderRecord 'canceled'.
    assert _parse_already_terminal_state(_gateway_already_terminal_reject("cancelled")) == "canceled"
    # Non-terminal rejects must NOT match — they fall through to retry/backoff.
    assert _parse_already_terminal_state({"error": "rate limited", "status_code": 429}) is None
    assert _parse_already_terminal_state({"detail": "order not found", "error": "404"}) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_already_filled_reject_reconciles_without_alert(monkeypatch) -> None:
    """A stale cancel rejected because the broker says the order is ALREADY FILLED
    (poll_fills' 200-row status window aged the order out before it saw the fill)
    must be reconciled — flip the row terminal, drop the pending reservation — and
    must NOT back off, retry, or page the false 'reserving DTBP until close' alert.
    The order is filled, not stuck; this was the 2026-06-22 Discord storm (182
    already-filled orders each gave up + paged, tripping Discord's 429 limit)."""
    _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    sent: list[str] = []

    async def _fake_alert(message, *, dedupe_key=None):
        sent.append(dedupe_key or message)
        return True

    monkeypatch.setattr(mod, "send_discord_alert", _fake_alert, raising=False)

    status_updates: list[tuple[str, str]] = []

    async def _fake_status_update(*, broker_order_id, status, **kw):
        status_updates.append((broker_order_id, status))

    monkeypatch.setattr(mod, "persist_order_status_update", _fake_status_update, raising=False)

    ee = _engine()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "SPCX"}]
    )
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value=_gateway_already_terminal_reject("filled"))

    n = await ee._cancel_stale_entry_orders(client)

    # Reconciled to the broker's terminal state — not cancelled, not failed:
    assert ("b-1", "filled") in status_updates  # row flipped out of the open set
    ee._remove_pending_order_compat.assert_awaited()  # DTBP reservation dropped
    assert "b-1" not in ee._cancel_attempts  # no backoff/give-up state armed
    assert sent == []  # NO false 'reserving DTBP' page — this is the storm fix
    assert n == 0  # a reconcile is not a cancel


@pytest.mark.unit
@pytest.mark.asyncio
async def test_already_canceled_reject_reconciles_to_canceled(monkeypatch) -> None:
    """The reconcile generalises to any terminal broker state: a cancel rejected
    because the order is already 'canceled'/'expired' is a state-desync to
    reconcile, not a failure to retry-and-page."""
    _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    monkeypatch.setattr(mod, "persist_order_status_update", AsyncMock(), raising=False)

    ee = _engine()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-2", "client_order_id": "orion_b", "ticker": "EWY"}]
    )
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value=_gateway_already_terminal_reject("canceled"))

    await ee._cancel_stale_entry_orders(client)

    mod.persist_order_status_update.assert_awaited_once_with(broker_order_id="b-2", status="canceled")
    assert "b-2" not in ee._cancel_attempts


# ── cost-basis recovery: re-process the fill poll_fills' 200-row window missed ──


def _filled_order(
    broker_id: str = "b-1",
    coid: str = "orion_a",
    symbol: str = "SPCX",
    qty: float = 3,
    price: float = 2.5,
) -> dict[str, object]:
    """The broker order dict get_order returns for an already-filled order — the
    shape FillProcessor.process_single_fill consumes (Alpaca order fields)."""
    return {
        "id": broker_id,
        "client_order_id": coid,
        "symbol": symbol,
        "side": "buy",
        "qty": str(qty),
        "filled_qty": str(qty),
        "filled_avg_price": str(price),
        "status": "filled",
        "filled_at": "2026-06-22T15:00:53.956207Z",
    }


def _capture_status_updates(monkeypatch, mod) -> list[tuple[str, str]]:
    updates: list[tuple[str, str]] = []

    async def _fake(*, broker_order_id, status, **kw):
        updates.append((broker_order_id, status))

    monkeypatch.setattr(mod, "persist_order_status_update", _fake, raising=False)
    return updates


def _silence_alerts(monkeypatch, mod) -> list[str]:
    sent: list[str] = []

    async def _fake_alert(message, *, dedupe_key=None):
        sent.append(dedupe_key or message)
        return True

    monkeypatch.setattr(mod, "send_discord_alert", _fake_alert, raising=False)
    return sent


@pytest.mark.unit
@pytest.mark.asyncio
async def test_already_filled_reconcile_recovers_missed_fill(monkeypatch) -> None:
    """A stale cancel rejected 'already filled' means poll_fills' 200-row window
    never wrote a FillRecord for it, so _compute_cost_basis_from_fills can't
    reconstruct its cost basis. The reconcile now fetches the order by id and
    feeds it through the SAME idempotent processor poll uses — recovering the
    fill — while STILL flipping the row terminal (storm fix) and not paging."""
    _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    sent = _silence_alerts(monkeypatch, mod)
    status_updates = _capture_status_updates(monkeypatch, mod)

    ee = _engine()
    ee._process_single_fill = AsyncMock()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "SPCX"}]
    )
    order = _filled_order(broker_id="b-1", coid="orion_a", symbol="SPCX")
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value=_gateway_already_terminal_reject("filled"))
    client.get_order = AsyncMock(return_value=order)

    n = await ee._cancel_stale_entry_orders(client)

    # Recovered via a single by-id fetch through the idempotent fill processor:
    client.get_order.assert_awaited_once_with("b-1")
    ee._process_single_fill.assert_awaited_once_with(order)
    # ...and the storm fix is intact: row flipped terminal, pending dropped, no page.
    assert ("b-1", "filled") in status_updates
    ee._remove_pending_order_compat.assert_awaited()
    assert sent == []
    assert n == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recovery_get_order_error_still_reconciles_status(monkeypatch) -> None:
    """get_order returns {"error":...} (never raises) on 4xx/5xx/timeout. The fill
    stays unrecovered (logged), but the row is STILL flipped terminal so the
    cancel/alert storm cannot resume — a fetch failure must not strand the order
    back in the storming set."""
    _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    sent = _silence_alerts(monkeypatch, mod)
    status_updates = _capture_status_updates(monkeypatch, mod)

    ee = _engine()
    ee._process_single_fill = AsyncMock()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "SPCX"}]
    )
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value=_gateway_already_terminal_reject("filled"))
    client.get_order = AsyncMock(return_value={"error": "500", "detail": "gateway boom", "status_code": 500})

    n = await ee._cancel_stale_entry_orders(client)

    ee._process_single_fill.assert_not_awaited()  # nothing usable to process
    assert ("b-1", "filled") in status_updates  # storm fix preserved
    assert "b-1" not in ee._cancel_attempts  # not re-armed for retry
    assert sent == []
    assert n == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recovery_get_order_exception_does_not_break_sweep(monkeypatch) -> None:
    """A raised transport error in get_order must be swallowed (recovery is
    best-effort) and must not block the status reconcile."""
    _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    _silence_alerts(monkeypatch, mod)
    status_updates = _capture_status_updates(monkeypatch, mod)

    ee = _engine()
    ee._process_single_fill = AsyncMock()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "SPCX"}]
    )
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value=_gateway_already_terminal_reject("filled"))
    client.get_order = AsyncMock(side_effect=RuntimeError("transport down"))

    n = await ee._cancel_stale_entry_orders(client)  # must not raise

    ee._process_single_fill.assert_not_awaited()
    assert ("b-1", "filled") in status_updates
    assert n == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recovery_zero_filled_qty_skips_processing(monkeypatch) -> None:
    """Race: the cancel-reject said 'filled' but the order snapshot reports zero
    filled_qty. Skip processing (no phantom zero-qty fill) but still reconcile."""
    _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    _silence_alerts(monkeypatch, mod)
    status_updates = _capture_status_updates(monkeypatch, mod)

    ee = _engine()
    ee._process_single_fill = AsyncMock()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "SPCX"}]
    )
    order = _filled_order(broker_id="b-1")
    order["filled_qty"] = "0"
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value=_gateway_already_terminal_reject("filled"))
    client.get_order = AsyncMock(return_value=order)

    await ee._cancel_stale_entry_orders(client)

    ee._process_single_fill.assert_not_awaited()
    assert ("b-1", "filled") in status_updates


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_filled_terminal_reconcile_does_not_fetch_or_recover(monkeypatch) -> None:
    """Only a 'filled' desync has a fill to recover. A 'canceled'/'expired'/
    'rejected' reconcile must NOT fetch the order or touch the fill processor."""
    _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    _silence_alerts(monkeypatch, mod)
    status_updates = _capture_status_updates(monkeypatch, mod)

    ee = _engine()
    ee._process_single_fill = AsyncMock()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-2", "client_order_id": "orion_b", "ticker": "EWY"}]
    )
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value=_gateway_already_terminal_reject("canceled"))
    client.get_order = AsyncMock()

    await ee._cancel_stale_entry_orders(client)

    client.get_order.assert_not_awaited()  # no fill to recover for a non-filled state
    ee._process_single_fill.assert_not_awaited()
    assert ("b-2", "canceled") in status_updates


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recovery_through_real_processor_applies_full_qty_once(monkeypatch) -> None:
    """Drive the REAL FillProcessor (not a mocked _process_single_fill) so the
    recovery's field-flow and the f'{order_id}:{filled_qty}' idempotency marker are
    regression-guarded: the first recovery applies the FULL filled_qty (the stale
    set guarantees zero prior fills) and a second recovery of the same order is
    deduped — proving the double-count safety the storm-fix comment relies on."""
    from orion.execution.fill_processor import FillProcessor
    import orion.execution.fill_processor as fp_mod

    processed: set[str] = set()

    async def _is_processed(marker: str) -> bool:
        return marker in processed

    async def _mark(marker: str, **kw) -> None:
        processed.add(marker)

    async def _persist(_fill) -> None:
        return None

    monkeypatch.setattr(fp_mod, "is_fill_processed", _is_processed)
    monkeypatch.setattr(fp_mod, "mark_fill_processed", _mark)
    monkeypatch.setattr(fp_mod, "persist_fill_record", _persist)

    outcome = MagicMock()
    outcome.is_closing = False
    rm = MagicMock()
    rm.process_fill = AsyncMock(return_value=outcome)
    rm.update_sector_exposure = MagicMock()

    ee = _engine()
    ee.risk_manager = rm
    ee._fill_processor = FillProcessor()

    order = _filled_order(broker_id="b-1", coid="orion_a", symbol="SPCX", qty=3, price=2.5)
    client = AsyncMock()
    client.get_order = AsyncMock(return_value=order)

    assert await ee._recover_missed_fill(client, "b-1", "SPCX") is True
    rm.process_fill.assert_awaited_once()
    # incremental_qty == full filled_qty (positional arg 1), parsed from the string field
    assert rm.process_fill.await_args.args[1] == 3.0

    # A second recovery (or a later poll) of the same order must NOT re-apply it.
    await ee._recover_missed_fill(client, "b-1", "SPCX")
    rm.process_fill.assert_awaited_once()  # still once — deduped by the fill marker


@pytest.mark.unit
@pytest.mark.asyncio
async def test_filled_reconcile_not_refetched_next_sweep(monkeypatch) -> None:
    """Storm-safety invariant: after a filled reconcile flips the row terminal it
    leaves the open-status stale set, so a persistent already-filled order costs
    exactly ONE get_order total — never a per-sweep re-fetch loop."""
    _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    _silence_alerts(monkeypatch, mod)
    _capture_status_updates(monkeypatch, mod)

    ee = _engine()
    ee._process_single_fill = AsyncMock()
    # Sweep 1: stale + open. Sweep 2: the flip to 'filled' removed it from the set.
    ee._fetch_stale_entry_orders = AsyncMock(
        side_effect=[
            [{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "SPCX"}],
            [],
        ]
    )
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value=_gateway_already_terminal_reject("filled"))
    client.get_order = AsyncMock(return_value=_filled_order(broker_id="b-1"))

    await ee._cancel_stale_entry_orders(client)
    await ee._cancel_stale_entry_orders(client)

    assert client.get_order.await_count == 1  # fetched once across both sweeps


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_order_recovery_is_capped_per_sweep(monkeypatch) -> None:
    """Recovery's get_order is bound by the same per-cycle cap as cancel_order, so a
    flood of already-filled stale orders cannot fan out unbounded Gateway calls in
    one sweep (the 429-storm class this branch exists to prevent)."""
    _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    _silence_alerts(monkeypatch, mod)
    _capture_status_updates(monkeypatch, mod)

    ee = _engine()
    ee._process_single_fill = AsyncMock()
    cap = ee._CANCEL_MAX_PER_CYCLE
    many = [{"broker_order_id": f"b-{i}", "client_order_id": f"orion_{i}", "ticker": "SPCX"} for i in range(cap + 5)]
    ee._fetch_stale_entry_orders = AsyncMock(return_value=many)
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value=_gateway_already_terminal_reject("filled"))
    client.get_order = AsyncMock(side_effect=lambda bid: _filled_order(broker_id=bid))

    await ee._cancel_stale_entry_orders(client)

    assert client.get_order.await_count <= cap


# ── query logic (real test DB) ───────────────────────────────────────────────


async def _wipe_orders() -> None:
    async with async_session_factory() as session:
        for row in (await session.execute(select(OrderRecord))).scalars().all():
            await session.delete(row)
        await session.commit()


async def _add_order(**kw: object) -> None:
    async with async_session_factory() as session:
        session.add(OrderRecord(**kw))
        await session.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fetch_selects_only_stale_unfilled_orion_entries() -> None:
    await init_db()
    await _wipe_orders()

    ttl = ExecutionEngine._STALE_ENTRY_ORDER_TTL_SECONDS
    old = datetime.now(UTC) - timedelta(seconds=ttl + 60)
    fresh = datetime.now(UTC) - timedelta(seconds=5)

    # SHOULD match: stale, unfilled, orion entry that reached the broker.
    await _add_order(
        id="o1",
        ticker="EWY",
        side="buy",
        qty=7,
        client_order_id="orion_stale",
        broker_order_id="b-stale",
        status="new",
        created_at_utc=old,
    )
    # too fresh — still within its fill window.
    await _add_order(
        id="o2",
        ticker="XHB",
        side="buy",
        qty=1,
        client_order_id="orion_fresh",
        broker_order_id="b-fresh",
        status="new",
        created_at_utc=fresh,
    )
    # already filled.
    await _add_order(
        id="o3",
        ticker="SPY",
        side="buy",
        qty=1,
        client_order_id="orion_filled",
        broker_order_id="b-filled",
        status="filled",
        created_at_utc=old,
    )
    # already terminal.
    await _add_order(
        id="o4",
        ticker="DIA",
        side="buy",
        qty=1,
        client_order_id="orion_exp",
        broker_order_id="b-exp",
        status="expired",
        created_at_utc=old,
    )
    # not orion-attributed (sibling system on the shared account).
    await _add_order(
        id="o5",
        ticker="AAPL",
        side="buy",
        qty=1,
        client_order_id="cerb_x",
        broker_order_id="b-cerb",
        status="new",
        created_at_utc=old,
        system="cerberus",
    )
    # orion entry that never reached the broker (no broker_order_id to cancel).
    await _add_order(
        id="o6",
        ticker="MU",
        side="buy",
        qty=1,
        client_order_id="orion_nobroker",
        broker_order_id=None,
        status="new",
        created_at_utc=old,
    )
    # already filling — must never be cancelled out from under a partial fill.
    await _add_order(
        id="o7",
        ticker="QQQ",
        side="buy",
        qty=2,
        client_order_id="orion_partial",
        broker_order_id="b-partial",
        status="partially_filled",
        created_at_utc=old,
    )
    # crash-window sentinel (pre-broker tracking row) — excluded by status.
    await _add_order(
        id="o8",
        ticker="IWM",
        side="buy",
        qty=1,
        client_order_id="orion_pending_submit",
        broker_order_id="b-pendsub",
        status="PENDING_SUBMIT",
        created_at_utc=old,
    )
    # REJECTED close row written by persist_exit_order_rejection — proves the
    # `orders` table is not strictly entries-only, yet this is still excluded
    # (terminal status; broker_order_id is null).
    await _add_order(
        id="o9",
        ticker="NVDA",
        side="sell",
        qty=1,
        client_order_id="orion_rejected_close",
        broker_order_id=None,
        status="REJECTED",
        created_at_utc=old,
    )

    ee = ExecutionEngine.__new__(ExecutionEngine)
    rows = await ee._fetch_stale_entry_orders()

    coids = {r["client_order_id"] for r in rows}
    assert coids == {"orion_stale"}
    match = next(r for r in rows if r["client_order_id"] == "orion_stale")
    assert match["broker_order_id"] == "b-stale"
    assert match["ticker"] == "EWY"
