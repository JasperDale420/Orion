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
    """Bare engine; the DB query is stubbed so these target cancel logic only.

    The pre-cancel broker-state refresh (2026-08-17 fix) is stubbed to report
    every stale order as confirmed OPEN at the broker, so tests that only care
    about `cancel_order`'s response (rejection, success, backoff, ...) don't
    also need to wire up `get_orders`/`get_order`. Tests that exercise the
    refresh itself use `_engine_real_refresh()` instead.
    """
    ee = ExecutionEngine.__new__(ExecutionEngine)
    ee._remove_pending_order_compat = AsyncMock()
    # __new__ bypasses __init__, so the cancel-state dict must be seeded here
    # (the engine lazily seeds it too, but tests assert on it directly).
    ee._cancel_attempts = {}
    ee._refresh_open_broker_orders = AsyncMock(
        side_effect=lambda client, stale: {
            str(r["broker_order_id"]): {"id": str(r["broker_order_id"]), "status": "new"}
            for r in stale
            if r.get("broker_order_id")
        }
    )
    return ee


def _engine_real_refresh() -> ExecutionEngine:
    """Bare engine with the REAL pre-cancel broker-state refresh wired up, for
    tests that exercise the refresh (`get_orders`/`get_order`) itself."""
    ee = ExecutionEngine.__new__(ExecutionEngine)
    ee._remove_pending_order_compat = AsyncMock()
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


# ── legacy unowned order: cancel fail-closes 404 GW-E4404 (never cancellable) ──


def _gateway_legacy_unowned_reject() -> dict[str, object]:
    """The dict GatewayTradingClient returns when the gateway fail-closes a cancel
    of a pre-2026-05-20 raw `orion_<uuid>` order. The gateway added a per-client
    `c-<client>-` ownership prefix on 2026-05-20; legacy orders lack it, so its
    cancel/get-order guard returns 404 GW-E4404. `_request` sets `detail` to the
    raw gateway body and `status_code` to 404."""
    body = json.dumps(
        {
            "success": False,
            "error": {"code": "GW-E4404", "message": "order not found for client orion"},
        }
    )
    return {"error": "Client error '404 Not Found' for url ...", "detail": body, "status_code": 404}


@pytest.mark.unit
def test_is_legacy_unowned_cancel_rejection_matches_gw_e4404_only() -> None:
    """Only a 404 GW-E4404 (legacy unowned order) matches — a generic 404, a 429,
    an already-terminal reject, and a trading-capability reject must NOT, so the
    reconcile is scoped to the exact never-cancellable case (no over-broadening)."""
    from orion.execution.execution_engine import _is_legacy_unowned_cancel_rejection

    assert _is_legacy_unowned_cancel_rejection(_gateway_legacy_unowned_reject()) is True
    # A bare 404 with no GW-E4404 code may be legitimately retryable — must NOT match.
    assert _is_legacy_unowned_cancel_rejection({"detail": "order not found", "error": "404"}) is False
    assert _is_legacy_unowned_cancel_rejection({"error": "rate limited", "status_code": 429}) is False
    assert _is_legacy_unowned_cancel_rejection(_gateway_already_terminal_reject("filled")) is False
    assert _is_legacy_unowned_cancel_rejection({"detail": "GW-E2009 Trading capability required"}) is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_legacy_unowned_404_reconciles_and_stops_retrying(monkeypatch) -> None:
    """A cancel rejected 404 GW-E4404 means the order is a legacy pre-2026-05-20
    order the gateway can't confirm Orion owns — it can NEVER be cancelled here.
    The sweep must reconcile the orphaned row terminal (so it leaves the open set
    and is never re-selected — within this process AND across restarts), drop the
    pending reservation, NOT arm backoff, and NOT page the false 'reserving DTBP'
    alert. This is the GW-A4001/GW-E4404 retry flood (1,164 warnings, 6/24)."""
    _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    sent = _silence_alerts(monkeypatch, mod)
    status_updates = _capture_status_updates(monkeypatch, mod)

    ee = _engine()
    # Sweep 1: stale + open. Sweep 2: the terminal flip removed it from the set.
    ee._fetch_stale_entry_orders = AsyncMock(
        side_effect=[
            [{"broker_order_id": "b-1", "client_order_id": "orion_legacy", "ticker": "MU"}],
            [],
        ]
    )
    client = AsyncMock()
    client.cancel_order = AsyncMock(return_value=_gateway_legacy_unowned_reject())

    n1 = await ee._cancel_stale_entry_orders(client)
    n2 = await ee._cancel_stale_entry_orders(client)

    assert client.cancel_order.await_count == 1  # attempted once, NOT a 6-retry flood
    client.get_order.assert_not_awaited()  # no fill recovery — get_order also 404s for legacy
    assert ("b-1", "canceled") in status_updates  # row flipped out of the open set
    ee._remove_pending_order_compat.assert_awaited()  # stale reservation dropped
    assert "b-1" not in ee._cancel_attempts  # no backoff/give-up state armed
    assert sent == []  # NO false 'reserving DTBP' page
    assert n1 == 0 and n2 == 0  # a reconcile is not a cancel


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
async def test_non_filled_terminal_reconcile_checks_for_a_missed_fill_anyway(monkeypatch) -> None:
    """A 'canceled'/'expired'/'rejected' reconcile via the post-cancel-rejection
    path (no order payload in hand — the state came from parsing the rejection
    text) still checks for a missed fill, because an order can partially fill
    and THEN be canceled/expired: it reports 'canceled'/'expired' with a
    nonzero filled_qty, not 'partially_filled' (Alpaca only uses that status
    while still open). _recover_missed_fill's own zero-qty guard makes this
    safe to call unconditionally — here the broker snapshot has no usable
    filled_qty (get_order is unconfigured), so process_single_fill is still
    never touched, but the fetch itself now happens."""
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

    client.get_order.assert_awaited_once_with("b-2")  # now checks for a missed partial fill
    ee._process_single_fill.assert_not_awaited()  # ...but finds nothing usable to process
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
    import orion.execution.execution_engine as mod
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
    # _process_single_fill ALSO verifies via its own is_fill_processed import
    # (a separate name binding from fp_mod's) after delegating to
    # FillProcessor — patch both against the SAME shared `processed` set so
    # this test simulates one coherent idempotency store, not two.
    monkeypatch.setattr(mod, "is_fill_processed", _is_processed)

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


# ── pre-cancel broker-state refresh (2026-08-17 GW-E4301 freeze fix) ──────────
#
# Incident: entry order d67ea8ac (PLTR260821C00182500) FILLED at 14:27:35Z, but
# the stale-entry sweep's cancel went out at 14:29:11Z — one second before
# poll_fills recovered the fill (MISSED_FILL_RECOVERED / STALE_ENTRY_RECONCILED
# broker_state=filled). Alpaca replied 422 "order is already in filled state",
# and the Gateway's ownership guard treated that as an ambiguous broker
# mutation and froze the symbol (GW-E4301) — refusing every subsequent order
# on it, including Orion's closes. Seven symbols froze that day. The sweep must
# refresh the order's real broker state BEFORE sending the cancel, so it never
# sends a mutation for an order it hasn't confirmed is still open.


@pytest.mark.unit
@pytest.mark.asyncio
async def test_open_at_broker_uses_single_batched_call_and_cancels() -> None:
    """When the batched open-orders snapshot contains the stale order, the
    sweep confirms it with the ONE call (no per-order fallback) and cancels
    exactly as before this fix."""
    ee = _engine_real_refresh()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "EWY"}]
    )
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=[{"id": "b-1", "status": "new"}])
    client.cancel_order = AsyncMock(return_value={})

    n = await ee._cancel_stale_entry_orders(client)

    assert n == 1
    client.get_orders.assert_awaited_once_with(status="open", limit=500)
    client.get_order.assert_not_awaited()  # found in the batch — no fallback needed
    client.cancel_order.assert_awaited_once_with("b-1")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_all_open_costs_exactly_one_batched_call_regardless_of_stale_count() -> None:
    """The attack this guards against: N stale orders must not cost N GETs when
    they're all genuinely open — one batched `get_orders(status=open)` call
    covers the whole sweep."""
    ee = _engine_real_refresh()
    stale = [{"broker_order_id": f"b-{i}", "client_order_id": f"orion_{i}", "ticker": "EWY"} for i in range(8)]
    ee._fetch_stale_entry_orders = AsyncMock(return_value=stale)
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=[{"id": f"b-{i}"} for i in range(8)])
    client.cancel_order = AsyncMock(return_value={})

    n = await ee._cancel_stale_entry_orders(client)

    assert n == 8
    assert client.get_orders.await_count == 1
    client.get_order.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_filled_at_broker_skips_cancel_and_reconciles(monkeypatch) -> None:
    """The exact incident shape: the order is no longer in the open-orders
    snapshot and get_order confirms it FILLED. The sweep must never call
    cancel_order — that DELETE-on-a-filled-order is what froze the Gateway —
    and must instead reconcile through the existing terminal-state path."""
    _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    _silence_alerts(monkeypatch, mod)
    status_updates = _capture_status_updates(monkeypatch, mod)

    ee = _engine_real_refresh()
    ee._process_single_fill = AsyncMock()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "d67ea8ac", "client_order_id": "orion_a", "ticker": "PLTR"}]
    )
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=[])  # no longer open
    order = _filled_order(broker_id="d67ea8ac", coid="orion_a", symbol="PLTR")
    client.get_order = AsyncMock(return_value=order)

    n = await ee._cancel_stale_entry_orders(client)

    client.cancel_order.assert_not_awaited()  # the ambiguous mutation is never sent
    ee._process_single_fill.assert_awaited_once_with(order)
    assert ("d67ea8ac", "filled") in status_updates
    ee._remove_pending_order_compat.assert_awaited()
    assert "d67ea8ac" not in ee._cancel_attempts
    assert n == 0  # a reconcile is not a cancel


@pytest.mark.unit
@pytest.mark.asyncio
async def test_canceled_at_broker_skips_cancel_and_reconciles_without_fill_recovery(monkeypatch) -> None:
    """The reconcile generalises to any terminal broker status found by the
    refresh: 'canceled' has no fill to recover, so get_order is called exactly
    once (the state check) and _process_single_fill is never touched."""
    _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    _silence_alerts(monkeypatch, mod)
    status_updates = _capture_status_updates(monkeypatch, mod)

    ee = _engine_real_refresh()
    ee._process_single_fill = AsyncMock()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-2", "client_order_id": "orion_b", "ticker": "EWY"}]
    )
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=[])
    client.get_order = AsyncMock(return_value={"id": "b-2", "status": "canceled"})

    n = await ee._cancel_stale_entry_orders(client)

    client.cancel_order.assert_not_awaited()
    client.get_order.assert_awaited_once_with("b-2")
    ee._process_single_fill.assert_not_awaited()
    assert ("b-2", "canceled") in status_updates
    assert n == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_done_for_day_at_broker_reconciles(monkeypatch) -> None:
    """`done_for_day` is one of the terminal states the task explicitly calls
    out alongside filled/canceled/expired/rejected."""
    _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    _silence_alerts(monkeypatch, mod)
    status_updates = _capture_status_updates(monkeypatch, mod)

    ee = _engine_real_refresh()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-3", "client_order_id": "orion_c", "ticker": "SPY"}]
    )
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=[])
    client.get_order = AsyncMock(return_value={"id": "b-3", "status": "done_for_day"})

    n = await ee._cancel_stale_entry_orders(client)

    client.cancel_order.assert_not_awaited()
    assert ("b-3", "done_for_day") in status_updates
    assert n == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_partially_filled_at_broker_treated_as_open_and_still_cancels() -> None:
    """A partial fill DB hasn't caught up on yet is still OPEN — Alpaca only
    reports `partially_filled` while unfilled quantity remains — so the sweep
    must still cancel it, targeting the remainder, exactly as before this fix.
    (In practice Alpaca's own `status=open` filter already includes a
    partially_filled order, so this exercises the classification directly via
    the bounded per-order fallback.)"""
    ee = _engine_real_refresh()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "QQQ"}]
    )
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=[])
    client.get_order = AsyncMock(return_value={"id": "b-1", "status": "partially_filled"})
    client.cancel_order = AsyncMock(return_value={})

    n = await ee._cancel_stale_entry_orders(client)

    assert n == 1
    client.cancel_order.assert_awaited_once_with("b-1")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fallback_observed_partial_fill_is_recovered_before_cancelling_remainder() -> None:
    """[high, from adversarial review round 2] A still-OPEN order discovered
    via the bounded per-order fallback can already carry an executed quantity
    poll_fills hasn't caught up on. It must be recovered BEFORE the cancel
    targets the remainder — once cancelled the row leaves the stale set (a
    fresh status this sweep never re-selects) and that quantity would
    otherwise be lost forever, never landing in FillRecord/risk/cost basis."""
    ee = _engine_real_refresh()
    ee._process_single_fill = AsyncMock()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "QQQ"}]
    )
    order = {"id": "b-1", "status": "partially_filled", "filled_qty": "2", "qty": "5"}
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=[])
    client.get_order = AsyncMock(return_value=order)
    client.cancel_order = AsyncMock(return_value={})

    n = await ee._cancel_stale_entry_orders(client)

    assert n == 1
    ee._process_single_fill.assert_awaited_once_with(order)  # the executed 2 IS recovered
    client.cancel_order.assert_awaited_once_with("b-1")  # ...and the remainder is still cancelled


@pytest.mark.unit
@pytest.mark.asyncio
async def test_batch_observed_partial_fill_is_recovered_before_cancelling_remainder() -> None:
    """[high, from adversarial review round 2] Same as above but the partial
    fill is observed via the ONE batched get_orders(status=open) call, not
    the per-order fallback — Alpaca lists a partially_filled order under
    status=open with its real filled_qty on the payload. The batch path must
    retain that payload (not just the order id) so the executed quantity is
    recovered before the cancel — previously the batch path reduced every
    open order to bare membership and threw the payload away, silently
    losing this exact quantity on a successful cancel."""
    ee = _engine_real_refresh()
    ee._process_single_fill = AsyncMock()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "QQQ"}]
    )
    order = {"id": "b-1", "status": "partially_filled", "filled_qty": "2", "qty": "5"}
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=[order])  # found in the batch itself
    client.cancel_order = AsyncMock(return_value={})

    n = await ee._cancel_stale_entry_orders(client)

    assert n == 1
    client.get_order.assert_not_awaited()  # no fallback needed — found in the batch
    ee._process_single_fill.assert_awaited_once_with(order)  # the executed 2 IS recovered
    client.cancel_order.assert_awaited_once_with("b-1")  # ...and the remainder is still cancelled


@pytest.mark.unit
@pytest.mark.asyncio
async def test_batch_observed_partial_fill_recovery_failure_defers_the_cancel() -> None:
    """[high, from adversarial review round 3] If recovering the partial fill
    genuinely fails (not just 'nothing to recover'), the cancel must NOT be
    sent this cycle — cancelling would drop the row out of the stale set
    (this sweep never re-selects it again) and permanently lose that
    quantity. Unlike a terminal reconcile (where the broker has ALREADY
    finalized the order, so the status flip proceeds regardless — the
    2026-06-22 storm fix), sending this cancel is a mutation Orion controls
    and can simply defer to next sweep instead."""
    ee = _engine_real_refresh()
    ee._process_single_fill = AsyncMock(side_effect=RuntimeError("db write failed"))
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "QQQ"}]
    )
    order = {"id": "b-1", "status": "partially_filled", "filled_qty": "2", "qty": "5"}
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=[order])
    client.cancel_order = AsyncMock(return_value={})

    n = await ee._cancel_stale_entry_orders(client)

    assert n == 0
    client.cancel_order.assert_not_awaited()  # never sent — the quantity would be lost
    ee._remove_pending_order_compat.assert_not_awaited()  # reservation kept — retry next sweep


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fallback_observed_partial_fill_recovery_failure_defers_the_cancel() -> None:
    """[high, from adversarial review round 3] Same as above via the bounded
    per-order fallback path (order absent from the batch)."""
    ee = _engine_real_refresh()
    ee._process_single_fill = AsyncMock(side_effect=RuntimeError("db write failed"))
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "QQQ"}]
    )
    order = {"id": "b-1", "status": "partially_filled", "filled_qty": "2", "qty": "5"}
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=[])
    client.get_order = AsyncMock(return_value=order)
    client.cancel_order = AsyncMock(return_value={})

    n = await ee._cancel_stale_entry_orders(client)

    assert n == 0
    client.cancel_order.assert_not_awaited()
    ee._remove_pending_order_compat.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_repeated_partial_fill_recovery_failure_eventually_gives_up_and_alerts(monkeypatch) -> None:
    """[high, from adversarial review round 4] A recovery that keeps failing
    (e.g. FillProcessor's in-memory partial-fill tracker already advanced
    past this quantity on the first failed attempt, so a retry can never
    re-attempt the write that failed) must not loop silently forever with
    the day-trading buying power reserved. It is routed through the SAME
    backoff/give-up/alert state machine a rejected cancel uses, so it ends in
    a durable operator alert after _CANCEL_MAX_ATTEMPTS."""
    clock = _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    sent: list[str] = []

    async def _fake_alert(message, *, dedupe_key=None):
        sent.append(dedupe_key or message)
        return True

    monkeypatch.setattr(mod, "send_discord_alert", _fake_alert, raising=False)

    ee = _engine_real_refresh()
    ee._process_single_fill = AsyncMock(side_effect=RuntimeError("db write failed"))
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "QQQ"}]
    )
    order = {"id": "b-1", "status": "partially_filled", "filled_qty": "2", "qty": "5"}
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=[order])
    client.cancel_order = AsyncMock(return_value={})

    max_attempts = mod.ExecutionEngine._CANCEL_MAX_ATTEMPTS
    for _ in range(max_attempts + 3):
        clock[0] += 10_000.0
        await ee._cancel_stale_entry_orders(client)

    client.cancel_order.assert_not_awaited()  # the ambiguous mutation is never sent, even after giving up
    assert ee._cancel_attempts["b-1"].gave_up is True
    assert len(sent) == 1  # one alert, not one per sweep


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apply_recovered_fill_rejects_nonnumeric_filled_qty_without_raising() -> None:
    """[medium, from adversarial review round 2] A malformed broker
    filled_qty must never raise out of the 'NEVER raises' recovery helper —
    it is untrusted input from the Gateway/broker response."""
    ee = _engine_real_refresh()
    ee._process_single_fill = AsyncMock()

    ok = await ee._apply_recovered_fill({"filled_qty": "not-a-number"}, "b-1", "EWY")

    assert ok is False
    ee._process_single_fill.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apply_recovered_fill_rejects_non_finite_filled_qty_without_raising() -> None:
    """[medium, from adversarial review round 2] NaN passes a naive `<= 0`
    guard as False (NaN comparisons are always False) and infinity passes it
    as True — both must be rejected as invalid rather than fed to the fill
    processor or a risk calculation."""
    ee = _engine_real_refresh()
    ee._process_single_fill = AsyncMock()

    assert await ee._apply_recovered_fill({"filled_qty": "nan"}, "b-1", "EWY") is False
    assert await ee._apply_recovered_fill({"filled_qty": "inf"}, "b-1", "EWY") is False
    ee._process_single_fill.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unrecognized_broker_status_skips_cancel_conservatively() -> None:
    """A refreshed status that is neither a known-open nor a known-terminal
    marker must fail toward NOT cancelling rather than guess."""
    ee = _engine_real_refresh()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "EWY"}]
    )
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=[])
    client.get_order = AsyncMock(return_value={"id": "b-1", "status": "pending_replace"})
    client.cancel_order = AsyncMock(return_value={})

    n = await ee._cancel_stale_entry_orders(client)

    assert n == 0
    client.cancel_order.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refresh_sweep_failure_skips_all_cancels_and_retries_next_cycle() -> None:
    """If the batched refresh itself fails (Gateway error/timeout), fail toward
    NOT sending any cancel this cycle — never fall back to N individual
    lookups against a possibly-degraded Gateway. The order stays stale in the
    DB and is re-evaluated cleanly next sweep once the Gateway recovers."""
    ee = _engine_real_refresh()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[
            {"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "EWY"},
            {"broker_order_id": "b-2", "client_order_id": "orion_b", "ticker": "XHB"},
        ]
    )
    client = AsyncMock()
    client.get_orders = AsyncMock(side_effect=RuntimeError("gateway down"))
    client.cancel_order = AsyncMock(return_value={})

    n = await ee._cancel_stale_entry_orders(client)  # must not raise

    assert n == 0
    client.cancel_order.assert_not_awaited()
    client.get_order.assert_not_awaited()
    ee._remove_pending_order_compat.assert_not_awaited()
    assert "b-1" not in ee._cancel_attempts  # infra failure, not a broker rejection — no backoff armed
    assert "b-2" not in ee._cancel_attempts

    # Retried cleanly next cycle once the Gateway recovers.
    client.get_orders = AsyncMock(return_value=[{"id": "b-1"}, {"id": "b-2"}])
    n2 = await ee._cancel_stale_entry_orders(client)
    assert n2 == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refresh_per_order_failure_skips_only_that_order() -> None:
    """Only the order NOT found in the batched open snapshot needs a fallback
    get_order; if THAT lookup fails, only this order's cancel is skipped —
    a sibling confirmed open in the batch still cancels normally this cycle."""
    ee = _engine_real_refresh()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[
            {"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "EWY"},
            {"broker_order_id": "b-2", "client_order_id": "orion_b", "ticker": "XHB"},
        ]
    )
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=[{"id": "b-1"}])  # b-2 missing from the batch
    client.get_order = AsyncMock(return_value={"error": "500", "detail": "boom", "status_code": 500})
    client.cancel_order = AsyncMock(return_value={})

    n = await ee._cancel_stale_entry_orders(client)

    assert n == 1
    client.cancel_order.assert_awaited_once_with("b-1")  # confirmed open — cancelled
    client.get_order.assert_awaited_once_with("b-2")  # bounded: only the one missing from the batch
    assert "b-2" not in ee._cancel_attempts  # not penalized — refresh failure, not a rejection


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refresh_per_order_exception_does_not_break_sweep() -> None:
    """A raised transport error from the per-order fallback get_order must be
    swallowed (never crash the sweep) and must skip the cancel for that order."""
    ee = _engine_real_refresh()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "EWY"}]
    )
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=[])
    client.get_order = AsyncMock(side_effect=RuntimeError("transport down"))
    client.cancel_order = AsyncMock(return_value={})

    n = await ee._cancel_stale_entry_orders(client)  # must not raise

    assert n == 0
    client.cancel_order.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_residual_race_cancel_rejected_terminal_still_reconciles(monkeypatch) -> None:
    """The refresh confirms 'open', but the broker finalizes the order in the
    gap before the cancel lands — the residual race the pre-cancel refresh
    cannot close (the Gateway-side ownership-guard fix, tracked separately,
    covers this). The cancel is still sent (as the refresh found it open), gets
    rejected 'already filled', and must still reconcile via the shared path —
    proving the extraction didn't change this existing, still-necessary
    behavior."""
    _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    _silence_alerts(monkeypatch, mod)
    status_updates = _capture_status_updates(monkeypatch, mod)

    ee = _engine_real_refresh()
    ee._process_single_fill = AsyncMock()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "SPCX"}]
    )
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=[{"id": "b-1", "status": "new"}])  # confirmed open at refresh time
    client.cancel_order = AsyncMock(return_value=_gateway_already_terminal_reject("filled"))
    client.get_order = AsyncMock(return_value=_filled_order(broker_id="b-1"))

    n = await ee._cancel_stale_entry_orders(client)

    client.cancel_order.assert_awaited_once_with("b-1")  # the refresh said open, so it WAS sent
    assert ("b-1", "filled") in status_updates
    assert n == 0


# ── adversarial-review follow-ups (Codex, 2026-08-17) ──────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_per_order_fallback_is_bounded_by_the_per_cycle_cap(monkeypatch) -> None:
    """[high] The per-order fallback (for orders absent from the batched
    open-orders snapshot) must be bounded by the SAME per-sweep cap as a
    cancel — otherwise a batch that's missing many orders (e.g. truncated by
    the Gateway's page limit) turns N stale orders into N individual broker
    reads in one sweep, exactly the attack-list latency concern this fix
    exists to prevent."""
    _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    cap = mod.ExecutionEngine._CANCEL_MAX_PER_CYCLE
    ee = _engine_real_refresh()
    many = [{"broker_order_id": f"b-{i}", "client_order_id": f"orion_{i}", "ticker": "EWY"} for i in range(cap + 10)]
    ee._fetch_stale_entry_orders = AsyncMock(return_value=many)
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=[])  # NONE found in the batch — worst case
    client.get_order = AsyncMock(return_value={"status": "new"})
    client.cancel_order = AsyncMock(return_value={})

    await ee._cancel_stale_entry_orders(client)

    assert client.get_order.await_count <= cap
    assert client.cancel_order.await_count <= cap


@pytest.mark.unit
@pytest.mark.asyncio
async def test_partially_filled_then_canceled_recovers_the_partial_fill(monkeypatch) -> None:
    """[high] An order that partially filled and was THEN canceled reports
    'canceled' (not 'partially_filled' — Alpaca only uses that status while
    still open) with a nonzero filled_qty. This is the 'partially_filled-then-
    canceled' terminal case the requirement calls out by name; it must still
    recover the partial fill, not just flip the row to 'canceled' and drop
    it — otherwise Orion undercounts a real broker position."""
    _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    _silence_alerts(monkeypatch, mod)
    status_updates = _capture_status_updates(monkeypatch, mod)

    ee = _engine_real_refresh()
    ee._process_single_fill = AsyncMock()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "SPCX"}]
    )
    order = _filled_order(broker_id="b-1", coid="orion_a", symbol="SPCX", qty=5, price=2.0)
    order["status"] = "canceled"
    order["filled_qty"] = "2"  # 2 of 5 filled before the rest was canceled
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=[])  # not open anymore
    client.get_order = AsyncMock(return_value=order)

    n = await ee._cancel_stale_entry_orders(client)

    client.cancel_order.assert_not_awaited()
    client.get_order.assert_awaited_once_with("b-1")  # ONE fetch — reused, not double-fetched
    ee._process_single_fill.assert_awaited_once_with(order)  # the partial fill IS recovered
    assert ("b-1", "canceled") in status_updates
    assert n == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_terminal_reconcile_with_payload_never_double_fetches(monkeypatch) -> None:
    """[efficiency, from the same review] When the pre-cancel refresh already
    fetched the order to learn its status, recovering a 'filled' desync must
    reuse that payload rather than fetching it again inside
    _recover_missed_fill — exactly one get_order call total."""
    _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    _silence_alerts(monkeypatch, mod)
    _capture_status_updates(monkeypatch, mod)

    ee = _engine_real_refresh()
    ee._process_single_fill = AsyncMock()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "SPCX"}]
    )
    order = _filled_order(broker_id="b-1")
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=[])
    client.get_order = AsyncMock(return_value=order)

    await ee._cancel_stale_entry_orders(client)

    assert client.get_order.await_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_legacy_unowned_via_pre_cancel_refresh_reconciles_without_looping(monkeypatch) -> None:
    """[high] A legacy pre-2026-05-20 order is absent from the ownership-scoped
    open-orders batch and 404s GW-E4404 on the individual get_order fallback.
    Before this fix that surfaced as a generic 'refresh unavailable' warning
    every sweep forever, bypassing the existing legacy-unowned reconciliation
    (which the cancel-rejection path already had) and never clearing the row
    or its pending reservation. It must now reconcile via the same path,
    without ever sending a cancel."""
    _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    sent = _silence_alerts(monkeypatch, mod)
    status_updates = _capture_status_updates(monkeypatch, mod)

    ee = _engine_real_refresh()
    ee._fetch_stale_entry_orders = AsyncMock(
        side_effect=[
            [{"broker_order_id": "b-1", "client_order_id": "orion_legacy", "ticker": "MU"}],
            [],  # sweep 2: the reconcile removed it from the stale set
        ]
    )
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=[])  # ownership-scoped batch never includes it
    client.get_order = AsyncMock(return_value=_gateway_legacy_unowned_reject())
    client.cancel_order = AsyncMock(return_value={})

    n1 = await ee._cancel_stale_entry_orders(client)
    n2 = await ee._cancel_stale_entry_orders(client)

    client.cancel_order.assert_not_awaited()  # never attempted — confirmed unreachable up front
    assert ("b-1", "canceled") in status_updates
    assert "b-1" not in ee._cancel_attempts
    assert sent == []
    assert n1 == 0 and n2 == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_replaced_status_at_broker_reconciles(monkeypatch) -> None:
    """[medium] `replaced` matches the broker-status vocabulary
    `decision_persistence._FAILED_BROKER_STATUSES` already treats as dead —
    it must reconcile like any other terminal state rather than being stuck
    as 'unrecognized' and retried forever."""
    _patch_clock(monkeypatch)
    import orion.execution.execution_engine as mod

    _silence_alerts(monkeypatch, mod)
    status_updates = _capture_status_updates(monkeypatch, mod)

    ee = _engine_real_refresh()
    ee._fetch_stale_entry_orders = AsyncMock(
        return_value=[{"broker_order_id": "b-1", "client_order_id": "orion_a", "ticker": "EWY"}]
    )
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=[])
    client.get_order = AsyncMock(return_value={"id": "b-1", "status": "replaced"})

    n = await ee._cancel_stale_entry_orders(client)

    client.cancel_order.assert_not_awaited()
    assert ("b-1", "replaced") in status_updates
    assert n == 0


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
