"""Entry-context resolution must never block or monopolise the monitor loop.

2026-08-17 incident: the position monitor reached 4.8 GB RSS and ~74 % CPU
while its exit-evaluation cadence degraded from the configured 60 s to
110-420 s. Root cause: ``_fetch_entry_context`` enriches through
``enrich_flow_for_scoring``, which fans out to synchronous ``HeberReader``
parquet scans (up to 370 days of silver bars: measured 80 s wall / 7.3 GB
peak for one symbol) invoked *directly on the event loop*.

Two consequences, both covered here:

1. A blocking call cannot be pre-empted by ``asyncio.wait_for``, so the 2 s
   fetch budget never fired on time — it was measured raising ``TimeoutError``
   85.7 s after the cycle started. The whole loop, including exit evaluation,
   was frozen for the duration.
2. Because the fetch "timed out", its result was deliberately never cached,
   so the entire scan cascade re-ran every cycle for every position, forever
   (714 timeouts on 2026-08-17, none of which ever resolved).

The pre-existing tests missed this because they simulated a slow fetch with
``await asyncio.sleep(...)``, which *yields* to the loop. Real parquet I/O
does not. These tests use a genuinely blocking ``time.sleep`` instead.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from orion.execution.position_monitor import PositionMonitor
from orion.storage.db import async_session_factory
from orion.storage.models_execution import OrderRecord
from orion.storage.models_gold import CandidateTrade, StrategyDecision

# Long enough that a blocking call is unmistakable against the heartbeat
# interval, short enough to keep the suite fast.
BLOCKING_SECONDS = 0.6
HEARTBEAT_INTERVAL = 0.02


async def _insert_orion_decision(option_symbol: str, ticker: str, dte_days: int = 14) -> str:
    """Insert a matching Orion EXECUTE decision so the enrichment branch runs.

    ``_fetch_entry_context`` only calls the enricher when the join resolves an
    ``entry_time``, so the row is required to exercise the real code path.
    """
    decision_ts = datetime.now(UTC) - timedelta(minutes=5)
    expiration = decision_ts + timedelta(days=dte_days)
    candidate_id = "cand_" + uuid.uuid4().hex[:16]
    decision_id = "dec_" + uuid.uuid4().hex[:16]

    async with async_session_factory() as session:
        session.add(
            CandidateTrade(
                candidate_id=candidate_id,
                ticker=ticker,
                timestamp_utc=decision_ts,
                rule_id="rule_bullish_sweep_v1",
                direction="LONG",
                confidence=0.7,
                source="UW",
                option_symbol=option_symbol,
                strike_price=200.0,
                expiration_date=expiration,
                option_type="C",
                underlying_price=195.0,
                premium=12500.0,
                evidence={"dte": dte_days, "is_sweep": True, "event_id": "evt_1"},
            )
        )
        session.add(
            StrategyDecision(
                decision_id=decision_id,
                candidate_id=candidate_id,
                timestamp_utc=decision_ts,
                ticker=ticker,
                strategy_version_id="test_v1",
                decision="EXECUTE",
                p_take=0.65,
                reason="test",
                executed_successfully="TRUE",
                decision_trace_json={},
            )
        )
        session.add(
            OrderRecord(
                id=str(uuid.uuid4()),
                created_at_utc=decision_ts,
                decision_id=decision_id,
                candidate_id=candidate_id,
                ticker=ticker,
                side="buy",
                qty=1.0,
                limit_price=1.25,
                client_order_id="orion_" + uuid.uuid4().hex,
                status="filled",
                system="orion",
                raw_json={},
            )
        )
        await session.commit()
    return decision_id


def _connector(symbol: str, unrealized_plpc: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(
        get_all_positions=lambda: [
            SimpleNamespace(
                symbol=symbol,
                current_price=2.0 * (1 + unrealized_plpc),
                avg_entry_price=2.0,
                qty=1.0,
                unrealized_plpc=unrealized_plpc,
            )
        ]
    )


async def _blocking_then_yield(seconds: float) -> None:
    """Model one enrichment fan-out: blocking I/O, then a yield.

    ``enrich_flow_for_scoring`` blocks the loop inside a sub-task and then
    returns to ``asyncio.gather``'s yield point, which is where an already
    expired ``wait_for`` timer finally gets to fire. Reproducing that yield is
    what makes the fetch appear to "time out" after the work is done, so the
    result is discarded and never cached.
    """
    time.sleep(seconds)
    # A real (not bare) yield, so an already-expired wait_for timer is
    # guaranteed to run before this coroutine resumes — exactly what happens
    # in production between two of the eleven gathered enrichment sub-tasks.
    await asyncio.sleep(0.001)


async def _drain(monitor: PositionMonitor) -> None:
    """Let any in-flight background resolution finish before the test ends."""
    pending = list(getattr(monitor, "_entry_context_tasks", {}).values())
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_sync_positions_does_not_starve_event_loop_on_blocking_enrichment() -> None:
    """The monitor loop must keep running while entry context resolves.

    This is the incident's root cause. ``enrich_flow_for_scoring`` reaches
    synchronous parquet I/O; if that runs on the event loop, every other
    coroutine — including the exit evaluation that follows in the same cycle,
    and the service-lease heartbeat — is frozen for its full duration, and the
    ``wait_for`` budget meant to bound it cannot fire.
    """
    option_symbol = "AAPL260117C00200000"
    await _insert_orion_decision(option_symbol, "AAPL")

    monitor = PositionMonitor()
    monitor._ENTRY_CONTEXT_FETCH_TIMEOUT_SECONDS = 0.05

    async def _blocking_enricher(**_kwargs: object) -> dict[str, object]:
        # Stands in for HeberReader's synchronous pyarrow scans.
        time.sleep(BLOCKING_SECONDS)
        return {"iv_rank_at_entry": 1.0}

    ticks = 0
    stop = False

    async def _heartbeat() -> None:
        nonlocal ticks
        while not stop:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            ticks += 1

    beat = asyncio.create_task(_heartbeat())
    try:
        with patch("orion.ml.flow_enricher.enrich_flow_for_scoring", new=_blocking_enricher):
            started = time.perf_counter()
            await monitor.sync_positions(_connector(option_symbol))
            elapsed = time.perf_counter() - started
            await _drain(monitor)
    finally:
        stop = True
        beat.cancel()
        with pytest.raises(asyncio.CancelledError):
            await beat

    # sync_positions must respect its own budget rather than inheriting the
    # blocking call's duration.
    assert elapsed < BLOCKING_SECONDS, (
        f"sync_positions took {elapsed:.2f}s, at least as long as the blocking "
        f"enricher ({BLOCKING_SECONDS}s) — the fetch budget was not enforced"
    )
    # And the loop must have stayed live throughout.
    expected = int(BLOCKING_SECONDS / HEARTBEAT_INTERVAL) // 2
    assert ticks >= expected, (
        f"event loop ticked only {ticks} times during a {BLOCKING_SECONDS}s "
        f"enrichment (expected >= {expected}) — the loop was blocked"
    )


@pytest.mark.asyncio
async def test_entry_context_resolution_is_not_repeated_every_cycle() -> None:
    """An expensive resolution must be attempted once, not once per cycle.

    On 2026-08-17 the same nine symbols re-ran the full scan cascade on every
    cycle for fifteen hours because a timed-out fetch is never cached.
    """
    option_symbol = "MSFT260117C00400000"
    await _insert_orion_decision(option_symbol, "MSFT")

    monitor = PositionMonitor()
    monitor._ENTRY_CONTEXT_FETCH_TIMEOUT_SECONDS = 0.05
    calls = 0

    async def _blocking_enricher(**_kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        await _blocking_then_yield(BLOCKING_SECONDS)
        return {"iv_rank_at_entry": 1.0}

    with patch("orion.ml.flow_enricher.enrich_flow_for_scoring", new=_blocking_enricher):
        for _ in range(3):
            await monitor.sync_positions(_connector(option_symbol))
        await _drain(monitor)
        # A fourth cycle after resolution completed must hit the cache.
        await monitor.sync_positions(_connector(option_symbol))

    assert calls == 1, f"enrichment ran {calls} times across 4 cycles; expected exactly 1"
    assert option_symbol in monitor._entry_context_cache, "resolved context was never cached"


@pytest.mark.asyncio
async def test_repeated_cycles_do_not_accumulate_pending_tasks() -> None:
    """Backgrounding resolution must not leak a task per cycle."""
    option_symbol = "NVDA260117C00150000"
    await _insert_orion_decision(option_symbol, "NVDA")

    monitor = PositionMonitor()
    monitor._ENTRY_CONTEXT_FETCH_TIMEOUT_SECONDS = 0.01

    async def _blocking_enricher(**_kwargs: object) -> dict[str, object]:
        time.sleep(0.2)
        return {}

    with patch("orion.ml.flow_enricher.enrich_flow_for_scoring", new=_blocking_enricher):
        await monitor.sync_positions(_connector(option_symbol))
        baseline = len(asyncio.all_tasks())
        for _ in range(5):
            await monitor.sync_positions(_connector(option_symbol))
        peak = len(asyncio.all_tasks())
        await _drain(monitor)

    assert peak <= baseline, f"pending tasks grew {baseline} -> {peak} across 5 cycles"
    assert not monitor._entry_context_tasks, "resolution tasks were not cleaned up"


@pytest.mark.asyncio
async def test_zero_dte_keeps_occ_bucket_while_context_is_unresolved() -> None:
    """A 0DTE contract must never wait on entry context for its bucket.

    Backgrounding resolution means a position can run for minutes before its
    context lands; the bucket must come from the contract's own expiry so the
    0DTE cadence, stops and hard flatten arm immediately.
    """
    today = datetime.now(UTC).strftime("%y%m%d")
    symbol = f"SPY{today}C00500000"
    await _insert_orion_decision(symbol, "SPY", dte_days=30)

    monitor = PositionMonitor()
    monitor._ENTRY_CONTEXT_FETCH_TIMEOUT_SECONDS = 0.01

    async def _blocking_enricher(**_kwargs: object) -> dict[str, object]:
        await _blocking_then_yield(0.3)
        return {}

    with patch("orion.ml.flow_enricher.enrich_flow_for_scoring", new=_blocking_enricher):
        (pos,) = await monitor.sync_positions(_connector(symbol))
        assert pos.bucket == "0DTE", "0DTE position did not get its bucket from the OCC symbol"
        await _drain(monitor)


@pytest.mark.asyncio
async def test_closing_a_position_never_hands_the_scan_permit_to_a_second_scan() -> None:
    """Adversarial review [high]: cancellation must not release the scan permit.

    Entry-context resolution holds a one-slot semaphore because a single scan
    peaks above 7 GB. Cancelling the resolver would unwind that ``async with``
    and free the permit — but the ``asyncio.to_thread`` worker it was awaiting
    cannot be interrupted and keeps scanning. Close/reopen churn could then
    stack concurrent multi-gigabyte scans and recreate the incident. Counting
    asyncio tasks cannot see this; only counting workers actually inside the
    scan can, which is what this test does.
    """
    closing = "TSLA260117C00300000"
    opening = "AMD260117C00120000"
    await _insert_orion_decision(closing, "TSLA")
    await _insert_orion_decision(opening, "AMD")

    monitor = PositionMonitor()
    monitor._ENTRY_CONTEXT_FETCH_TIMEOUT_SECONDS = 0.01

    lock = threading.Lock()
    inflight = 0
    peak = 0

    async def _counting_enricher(**_kwargs: object) -> dict[str, object]:
        nonlocal inflight, peak
        with lock:
            inflight += 1
            peak = max(peak, inflight)
        try:
            # Runs on the worker thread, exactly like a real parquet scan.
            time.sleep(0.4)
        finally:
            with lock:
                inflight -= 1
        await asyncio.sleep(0.001)
        return {}

    with patch("orion.ml.flow_enricher.enrich_flow_for_scoring", new=_counting_enricher):
        # Cycle 1 starts the scan for `closing` and gives up waiting on it.
        await monitor.sync_positions(_connector(closing))
        # Cycle 2: `closing` is gone from the broker and `opening` appears —
        # the close/reopen churn that would free the permit early.
        await monitor.sync_positions(_connector(opening))
        await _drain(monitor)
        # Let any resolution that outlived its position finish too.
        for _ in range(60):
            with lock:
                if inflight == 0:
                    break
            await asyncio.sleep(0.05)

    assert peak == 1, f"{peak} scans ran concurrently; the one-slot permit was released early"


@pytest.mark.asyncio
async def test_context_resolved_after_close_is_not_applied_to_a_reopen() -> None:
    """A resolution outliving its position must not cache onto the reopen."""
    symbol = "COIN260117C00250000"
    await _insert_orion_decision(symbol, "COIN")

    monitor = PositionMonitor()
    monitor._ENTRY_CONTEXT_FETCH_TIMEOUT_SECONDS = 0.01

    async def _slow_enricher(**_kwargs: object) -> dict[str, object]:
        await _blocking_then_yield(0.3)
        return {}

    with patch("orion.ml.flow_enricher.enrich_flow_for_scoring", new=_slow_enricher):
        await monitor.sync_positions(_connector(symbol))
        # Position closes while its resolution is still running.
        await monitor.sync_positions(SimpleNamespace(get_all_positions=list))
        await _drain(monitor)

    assert symbol not in monitor._entry_context_cache, (
        "a context resolved for a closed position was cached and would be reused by a reopen"
    )
    assert symbol not in monitor._entry_context_applied

    # A reopen of the same contract must resolve afresh rather than inherit the
    # closed position's context, and must not be starved by the drained task
    # still sitting in the task map.
    resolved = {
        "decision_id": "dec-reopen",
        "option_symbol": symbol,
        "bucket": "SWING",
        "direction": "LONG",
        "entry_time": datetime.now(UTC),
        "expiry_date": None,
    }

    async def _fast(sym: str) -> dict:
        monitor._entry_context_cache[sym] = resolved
        return resolved

    monitor._fetch_entry_context = _fast  # type: ignore[method-assign]
    (reopened,) = await monitor.sync_positions(_connector(symbol))
    await _drain(monitor)

    assert reopened.decision_id == "dec-reopen", "reopened position never got a fresh resolution"


@pytest.mark.asyncio
async def test_position_check_complete_reports_process_rss() -> None:
    """Cheap growth guard: the cycle summary must carry process RSS."""
    monitor = PositionMonitor()
    summary = await monitor.run_check(SimpleNamespace(get_all_positions=lambda: []), dry_run=True)

    assert "rss_mb" in summary, "position_check_complete summary is missing rss_mb"
    assert isinstance(summary["rss_mb"], float)
    assert summary["rss_mb"] > 0
