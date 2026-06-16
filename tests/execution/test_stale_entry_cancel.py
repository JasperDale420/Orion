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

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

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
    assert len(sent) == 1  # exactly one give-up alert

    # No matter how much time passes, a gave_up order is never retried again.
    import orion.execution.execution_engine as mod2

    mod2.time.monotonic = lambda: 10**9
    n2 = await ee._cancel_stale_entry_orders(client)
    assert n2 == 0
    assert client.cancel_order.await_count == 1  # never retried
    assert len(sent) == 1  # no second alert


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
    """If the Discord page is not delivered (send returns False — no webhook /
    dedupe / failure), the order must still give up cleanly and not crash; the
    durable ERROR log is the operator's guaranteed signal."""
    _patch_clock(monkeypatch)
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
    client.cancel_order = AsyncMock(
        return_value={
            "error": "Client error '403 Forbidden'",
            "detail": "GW-E2009 Trading capability required",
            "status_code": 403,
        }
    )

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
