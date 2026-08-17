"""Missed CLOSING fills must be recovered from a broker-vs-fills reconcile.

A successful close never gets an ``orders`` row (it persists to
``exit_decisions``), so the stale-entry sweep — which keys off ``OrderRecord`` —
can never surface a close that aged out of ``poll_fills``' 200-row window before
it was processed. Without a separate reconcile, that close's ``FillRecord`` never
lands and ``_compute_cost_basis_from_fills`` keeps replaying a position the
broker has already closed: realized PnL / cost basis stay incomplete.

``_recover_missed_close_fills`` detects the gap (fills magnitude > broker
magnitude for an Orion symbol) and recovers via the same idempotent
``_recover_missed_fill`` the entry sweep uses — bounded, attribution-safe, and
guarded against the partial double-count.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///:memory:")

from orion.execution.execution_engine import ExecutionEngine


def _engine() -> ExecutionEngine:
    """Bare engine; the broker client + cost-basis replay are stubbed so these
    target the close-reconcile logic only."""
    ee = ExecutionEngine.__new__(ExecutionEngine)
    # _recover_missed_fill is exercised on its own in test_stale_entry_cancel.py;
    # here it is the recovery primitive and is mocked unless a test drives it real.
    ee._recover_missed_fill = AsyncMock(return_value=True)
    return ee


def _all_unprocessed(monkeypatch) -> None:
    """No order has a prior processed fill (the common missed-close case)."""
    import orion.execution.execution_engine as mod

    monkeypatch.setattr(mod, "has_processed_fill_for_order", AsyncMock(return_value=False))


def _activity(order_id: str, symbol: str) -> dict[str, object]:
    """An Alpaca FILL account-activity: carries order_id + symbol, no client_order_id."""
    return {"activity_type": "FILL", "order_id": order_id, "symbol": symbol, "qty": "3", "price": "2.5"}


# ── detection ────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fills_exceed_broker_recovers_the_unprocessed_close(monkeypatch) -> None:
    """Fills replay a +3 long the broker has fully closed (flat). The reducing
    fill never landed → recover the close order, skipping the entry (already
    processed)."""
    _all_unprocessed(monkeypatch)
    import orion.execution.execution_engine as mod

    # Entry order is already processed; close order is not.
    monkeypatch.setattr(mod, "has_processed_fill_for_order", AsyncMock(side_effect=lambda oid: oid == "o-entry"))

    ee = _engine()
    ee._compute_cost_basis_from_fills = AsyncMock(return_value={"SPCX": {"qty": 3.0, "avg_entry": 2.5}})
    client = AsyncMock()
    client.get_positions = AsyncMock(return_value=[])  # broker flat
    client.get_account_activities = AsyncMock(return_value=[_activity("o-entry", "SPCX"), _activity("o-close", "SPCX")])

    n = await ee._recover_missed_close_fills(client)

    assert n == 1
    ee._recover_missed_fill.assert_awaited_once_with(client, "o-close", "SPCX")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_partial_close_recovers_remaining(monkeypatch) -> None:
    """Broker holds +2 of an original +5 long; the close order that took the
    other 3 aged out. fills(5) > broker(2) → recover."""
    _all_unprocessed(monkeypatch)

    ee = _engine()
    ee._compute_cost_basis_from_fills = AsyncMock(return_value={"SPCX": {"qty": 5.0, "avg_entry": 2.5}})
    client = AsyncMock()
    client.get_positions = AsyncMock(return_value=[{"symbol": "SPCX", "qty": "2"}])
    client.get_account_activities = AsyncMock(return_value=[_activity("o-close", "SPCX")])

    assert await ee._recover_missed_close_fills(client) == 1
    ee._recover_missed_fill.assert_awaited_once_with(client, "o-close", "SPCX")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_short_close_detected(monkeypatch) -> None:
    """A buy-to-close on a -4 short that the broker shows flat is a missed close
    too (magnitude test is sign-agnostic)."""
    _all_unprocessed(monkeypatch)

    ee = _engine()
    ee._compute_cost_basis_from_fills = AsyncMock(return_value={"XYZ": {"qty": -4.0, "avg_entry": 1.0}})
    client = AsyncMock()
    client.get_positions = AsyncMock(return_value=[])
    client.get_account_activities = AsyncMock(return_value=[_activity("o-close", "XYZ")])

    assert await ee._recover_missed_close_fills(client) == 1


# ── non-triggers ─────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agreement_is_noop_and_skips_activities(monkeypatch) -> None:
    """Fills == broker → nothing missed; the activities surface is never touched."""
    _all_unprocessed(monkeypatch)

    ee = _engine()
    ee._compute_cost_basis_from_fills = AsyncMock(return_value={"SPCX": {"qty": 3.0, "avg_entry": 2.5}})
    client = AsyncMock()
    client.get_positions = AsyncMock(return_value=[{"symbol": "SPCX", "qty": "3"}])
    client.get_account_activities = AsyncMock()

    assert await ee._recover_missed_close_fills(client) == 0
    client.get_account_activities.assert_not_awaited()
    ee._recover_missed_fill.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missed_entry_direction_is_ignored(monkeypatch) -> None:
    """broker magnitude > fills is a missed ENTRY (stale-entry sweep's job), not
    a close — this reconcile must not act on it."""
    _all_unprocessed(monkeypatch)

    ee = _engine()
    ee._compute_cost_basis_from_fills = AsyncMock(return_value={"SPCX": {"qty": 2.0, "avg_entry": 2.5}})
    client = AsyncMock()
    client.get_positions = AsyncMock(return_value=[{"symbol": "SPCX", "qty": "5"}])
    client.get_account_activities = AsyncMock()

    assert await ee._recover_missed_close_fills(client) == 0
    client.get_account_activities.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_fills_is_noop_no_broker_call(monkeypatch) -> None:
    """No held positions in the fills replay → no broker round-trip at all."""
    _all_unprocessed(monkeypatch)

    ee = _engine()
    ee._compute_cost_basis_from_fills = AsyncMock(return_value={})
    client = AsyncMock()
    client.get_positions = AsyncMock()

    assert await ee._recover_missed_close_fills(client) == 0
    client.get_positions.assert_not_awaited()


# ── safety: double-count guard, attribution, bounds ──────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_order_with_prior_fill_is_not_re_fed(monkeypatch) -> None:
    """A close order that already had a partial fill processed must NOT be
    re-fed — pushing the full order through again would double-count the close.
    The remaining gap is left for reconcile_pnl to route to BROKER_UNAVAILABLE."""
    import orion.execution.execution_engine as mod

    monkeypatch.setattr(mod, "has_processed_fill_for_order", AsyncMock(return_value=True))

    ee = _engine()
    ee._compute_cost_basis_from_fills = AsyncMock(return_value={"SPCX": {"qty": 5.0, "avg_entry": 2.5}})
    client = AsyncMock()
    client.get_positions = AsyncMock(return_value=[{"symbol": "SPCX", "qty": "2"}])
    client.get_account_activities = AsyncMock(return_value=[_activity("o-close", "SPCX")])

    assert await ee._recover_missed_close_fills(client) == 0
    ee._recover_missed_fill.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_orion_order_is_not_counted(monkeypatch) -> None:
    """An activity for the same symbol owned by a sibling system reaches
    _recover_missed_fill, which (via _process_single_fill's orion-prefix check)
    returns False — so it is fetched once but never counted as recovered."""
    _all_unprocessed(monkeypatch)

    ee = _engine()
    ee._recover_missed_fill = AsyncMock(return_value=False)  # not orion-owned → skipped inside
    ee._compute_cost_basis_from_fills = AsyncMock(return_value={"SPCX": {"qty": 3.0, "avg_entry": 2.5}})
    client = AsyncMock()
    client.get_positions = AsyncMock(return_value=[])
    client.get_account_activities = AsyncMock(return_value=[_activity("sibling-order", "SPCX")])

    assert await ee._recover_missed_close_fills(client) == 0
    ee._recover_missed_fill.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_order_recovery_is_capped_per_cycle(monkeypatch) -> None:
    """A wide disagreement cannot fan out unbounded get_order lookups — recovery
    is bound by _CLOSE_RECON_MAX_PER_CYCLE (the 429-storm class this guards)."""
    _all_unprocessed(monkeypatch)

    ee = _engine()
    cap = ee._CLOSE_RECON_MAX_PER_CYCLE
    ee._compute_cost_basis_from_fills = AsyncMock(return_value={"SPCX": {"qty": 99.0, "avg_entry": 2.5}})
    client = AsyncMock()
    client.get_positions = AsyncMock(return_value=[])
    client.get_account_activities = AsyncMock(return_value=[_activity(f"o-{i}", "SPCX") for i in range(cap + 5)])

    n = await ee._recover_missed_close_fills(client)
    assert ee._recover_missed_fill.await_count == cap
    assert n == cap


@pytest.mark.unit
@pytest.mark.asyncio
async def test_already_processed_orders_do_not_consume_the_cap(monkeypatch) -> None:
    """The double-count guard is a DB read, not a Gateway call, so already-processed
    orders must not eat into the per-cycle get_order budget."""
    import orion.execution.execution_engine as mod

    ee = _engine()
    cap = ee._CLOSE_RECON_MAX_PER_CYCLE
    # cap entries already processed (skipped free) followed by 2 recoverable.
    processed_ids = {f"done-{i}" for i in range(cap)}
    monkeypatch.setattr(mod, "has_processed_fill_for_order", AsyncMock(side_effect=lambda oid: oid in processed_ids))

    ee._compute_cost_basis_from_fills = AsyncMock(return_value={"SPCX": {"qty": 99.0, "avg_entry": 2.5}})
    client = AsyncMock()
    client.get_positions = AsyncMock(return_value=[])
    acts = [_activity(f"done-{i}", "SPCX") for i in range(cap)] + [
        _activity("close-a", "SPCX"),
        _activity("close-b", "SPCX"),
    ]
    client.get_account_activities = AsyncMock(return_value=acts)

    assert await ee._recover_missed_close_fills(client) == 2
    assert ee._recover_missed_fill.await_count == 2


# ── gone orders: 404 give-up (the 2026-07-02 GW-E4404 re-fetch storm) ────────


def _gone_404() -> dict[str, object]:
    """get_order's error dict for a Gateway GW-E4404 'Order not found'."""
    return {
        "error": "Client error '404 Not Found' for url ...",
        "detail": '{"success":false,"error":{"code":"GW-E4404","message":"Order not found."}}',
        "status_code": 404,
    }


def _engine_real_recovery() -> ExecutionEngine:
    """Engine with the REAL _recover_missed_fill (the give-up lives there)."""
    return ExecutionEngine.__new__(ExecutionEngine)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gone_order_404_is_fetched_once_per_session(monkeypatch) -> None:
    """A get_order 404 (legacy/unowned id that exists only on the activities
    surface) can never produce a ProcessedFill marker, so the marker dedupe
    never engages — without a give-up the recon re-fetches (and re-ERRORs) the
    same id every ~2min cycle forever. It must be fetched exactly once."""
    _all_unprocessed(monkeypatch)

    ee = _engine_real_recovery()
    ee._compute_cost_basis_from_fills = AsyncMock(return_value={"SPCX": {"qty": 3.0, "avg_entry": 2.5}})
    client = AsyncMock()
    client.get_positions = AsyncMock(return_value=[])
    client.get_account_activities = AsyncMock(return_value=[_activity("gone-1", "SPCX")])
    client.get_order = AsyncMock(return_value=_gone_404())

    assert await ee._recover_missed_close_fills(client) == 0
    assert client.get_order.await_count == 1

    # Subsequent cycles: same activities, but the gone id is never re-fetched.
    assert await ee._recover_missed_close_fills(client) == 0
    assert await ee._recover_missed_close_fills(client) == 0
    assert client.get_order.await_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bare_404_without_gw_e4404_is_not_cached_as_gone(monkeypatch) -> None:
    """Only the exact GW-E4404 code marks an id permanently gone (mirrors the
    cancel path's scoping). A 404 without it may be retryable — caching it would
    strand a recoverable close's cost basis until restart."""
    _all_unprocessed(monkeypatch)

    ee = _engine_real_recovery()
    ee._compute_cost_basis_from_fills = AsyncMock(return_value={"SPCX": {"qty": 3.0, "avg_entry": 2.5}})
    client = AsyncMock()
    client.get_positions = AsyncMock(return_value=[])
    client.get_account_activities = AsyncMock(return_value=[_activity("o-close", "SPCX")])
    client.get_order = AsyncMock(
        return_value={"error": "Client error '404 Not Found' for url ...", "detail": "not found", "status_code": 404}
    )

    assert await ee._recover_missed_close_fills(client) == 0
    assert await ee._recover_missed_close_fills(client) == 0
    assert client.get_order.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transient_get_order_error_is_retried_next_cycle(monkeypatch) -> None:
    """Only a 404 is permanent. A 5xx/timeout error dict must NOT be cached as
    gone — the fill may still be recoverable once the Gateway heals."""
    _all_unprocessed(monkeypatch)

    ee = _engine_real_recovery()
    ee._compute_cost_basis_from_fills = AsyncMock(return_value={"SPCX": {"qty": 3.0, "avg_entry": 2.5}})
    client = AsyncMock()
    client.get_positions = AsyncMock(return_value=[])
    client.get_account_activities = AsyncMock(return_value=[_activity("o-close", "SPCX")])
    client.get_order = AsyncMock(return_value={"error": "500", "detail": "gateway boom", "status_code": 500})

    assert await ee._recover_missed_close_fills(client) == 0
    assert await ee._recover_missed_close_fills(client) == 0
    assert client.get_order.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gone_orders_do_not_consume_the_per_cycle_cap(monkeypatch) -> None:
    """Once known-gone, an id is skipped before the cap check — a pile of legacy
    404 ids must not starve real recoveries out of the per-cycle budget."""
    _all_unprocessed(monkeypatch)

    ee = _engine_real_recovery()
    cap = ee._CLOSE_RECON_MAX_PER_CYCLE
    gone_ids = {f"gone-{i}" for i in range(cap)}
    ee._recon_gone_order_ids = set(gone_ids)
    ee._compute_cost_basis_from_fills = AsyncMock(return_value={"SPCX": {"qty": 99.0, "avg_entry": 2.5}})

    recoverable = {
        "id": "o-close",
        "client_order_id": "orion_close_a",
        "symbol": "SPCX",
        "side": "sell",
        "qty": "3",
        "filled_qty": "3",
        "filled_avg_price": "4.0",
        "status": "filled",
        "filled_at": "2026-06-22T20:00:00Z",
    }
    client = AsyncMock()
    client.get_positions = AsyncMock(return_value=[])
    client.get_account_activities = AsyncMock(
        return_value=[_activity(g, "SPCX") for g in sorted(gone_ids)] + [_activity("o-close", "SPCX")]
    )
    client.get_order = AsyncMock(return_value=recoverable)
    ee._process_single_fill = AsyncMock()

    assert await ee._recover_missed_close_fills(client) == 1
    client.get_order.assert_awaited_once_with("o-close")


# ── never raises ─────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_positions_failure_returns_zero(monkeypatch) -> None:
    _all_unprocessed(monkeypatch)

    ee = _engine()
    ee._compute_cost_basis_from_fills = AsyncMock(return_value={"SPCX": {"qty": 3.0, "avg_entry": 2.5}})
    client = AsyncMock()
    client.get_positions = AsyncMock(side_effect=RuntimeError("gateway down"))

    assert await ee._recover_missed_close_fills(client) == 0  # must not raise
    ee._recover_missed_fill.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_activities_failure_returns_zero(monkeypatch) -> None:
    _all_unprocessed(monkeypatch)

    ee = _engine()
    ee._compute_cost_basis_from_fills = AsyncMock(return_value={"SPCX": {"qty": 3.0, "avg_entry": 2.5}})
    client = AsyncMock()
    client.get_positions = AsyncMock(return_value=[])
    client.get_account_activities = AsyncMock(side_effect=RuntimeError("gateway down"))

    assert await ee._recover_missed_close_fills(client) == 0  # must not raise
    ee._recover_missed_fill.assert_not_awaited()


# ── wiring: detection drives the REAL recovery primitive once, idempotently ──


@pytest.mark.unit
@pytest.mark.asyncio
async def test_real_recovery_applies_close_fill_once(monkeypatch) -> None:
    """End-to-end with the REAL _recover_missed_fill + FillProcessor: a detected
    missed SELL close flows through the idempotent processor exactly once."""
    from orion.execution.fill_processor import FillProcessor
    import orion.execution.execution_engine as mod
    import orion.execution.fill_processor as fp_mod
    import orion.execution.persistence as persist_mod

    processed: set[str] = set()
    monkeypatch.setattr(fp_mod, "is_fill_processed", AsyncMock(side_effect=lambda m: m in processed))
    monkeypatch.setattr(fp_mod, "mark_fill_processed", AsyncMock(side_effect=lambda m, **k: processed.add(m)))
    monkeypatch.setattr(fp_mod, "persist_fill_record", AsyncMock())
    # allocate_exit_to_journal is a local import inside the processor, so
    # patch it on its own module. (is_closing close → journal attribution path.)
    monkeypatch.setattr(persist_mod, "allocate_exit_to_journal", AsyncMock())
    # The double-count guard reflects the real ProcessedFill state: once the
    # close's marker lands, a later cycle is skipped by the guard, not the
    # processor — exactly the production self-heal.
    monkeypatch.setattr(
        mod,
        "has_processed_fill_for_order",
        AsyncMock(side_effect=lambda oid: any(m.startswith(f"{oid}:") for m in processed)),
    )

    outcome = MagicMock()
    outcome.is_closing = True
    outcome.realized_pnl = 12.0
    rm = MagicMock()
    rm.process_fill = AsyncMock(return_value=outcome)
    rm.update_sector_exposure = MagicMock()

    ee = ExecutionEngine.__new__(ExecutionEngine)
    ee.risk_manager = rm
    ee._fill_processor = FillProcessor()
    ee._remove_pending_order_compat = AsyncMock()
    ee._compute_cost_basis_from_fills = AsyncMock(return_value={"SPCX": {"qty": 3.0, "avg_entry": 2.5}})

    close_order = {
        "id": "o-close",
        "client_order_id": "orion_close_a",
        "symbol": "SPCX",
        "side": "sell",
        "qty": "3",
        "filled_qty": "3",
        "filled_avg_price": "4.0",
        "status": "filled",
        "filled_at": "2026-06-22T20:00:00Z",
    }
    client = AsyncMock()
    client.get_positions = AsyncMock(return_value=[])
    client.get_account_activities = AsyncMock(return_value=[_activity("o-close", "SPCX")])
    client.get_order = AsyncMock(return_value=close_order)

    assert await ee._recover_missed_close_fills(client) == 1
    rm.process_fill.assert_awaited_once()
    assert rm.process_fill.await_args.args[1] == 3.0  # full close qty applied once

    # A second cycle of the same order must not re-apply it (marker dedupe).
    assert await ee._recover_missed_close_fills(client) == 0
    rm.process_fill.assert_awaited_once()
