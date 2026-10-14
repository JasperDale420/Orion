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
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from orion.execution.position_monitor import PositionMonitor, _expiry_from_occ_symbol
from orion.ml.exit_classifier import BucketExitClassifier
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


def _classifier_with(models: dict[str, object]) -> BucketExitClassifier:
    """A classifier carrying `models`, detached from the process singleton.

    `PositionMonitor.__init__` takes the singleton, so mutating
    `monitor.exit_classifier.models` in place would leak into every later test.
    """
    classifier = BucketExitClassifier()
    classifier.models = dict(models)
    return classifier


def _monitor_with_exit_model() -> PositionMonitor:
    """A monitor that will actually run entry-context enrichment.

    Enrichment is gated on a trained model for the position's own bucket, since
    its four features feed only that bucket's classifier vector. These tests are
    about how the enrichment behaves, so they need the gate open.
    """
    monitor = PositionMonitor()
    monitor.exit_classifier = _classifier_with({"SWING": object(), "0DTE": object(), "POSITION": object()})
    return monitor


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

    monitor = _monitor_with_exit_model()
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
        with patch("orion.ml.flow_enricher.enrich_flow_for_exit_features", new=_blocking_enricher):
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

    monitor = _monitor_with_exit_model()
    monitor._ENTRY_CONTEXT_FETCH_TIMEOUT_SECONDS = 0.05
    calls = 0

    async def _blocking_enricher(**_kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        await _blocking_then_yield(BLOCKING_SECONDS)
        return {"iv_rank_at_entry": 1.0}

    with patch("orion.ml.flow_enricher.enrich_flow_for_exit_features", new=_blocking_enricher):
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

    monitor = _monitor_with_exit_model()
    monitor._ENTRY_CONTEXT_FETCH_TIMEOUT_SECONDS = 0.01

    async def _blocking_enricher(**_kwargs: object) -> dict[str, object]:
        time.sleep(0.2)
        return {}

    with patch("orion.ml.flow_enricher.enrich_flow_for_exit_features", new=_blocking_enricher):
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

    monitor = _monitor_with_exit_model()
    monitor._ENTRY_CONTEXT_FETCH_TIMEOUT_SECONDS = 0.01

    async def _blocking_enricher(**_kwargs: object) -> dict[str, object]:
        await _blocking_then_yield(0.3)
        return {}

    with patch("orion.ml.flow_enricher.enrich_flow_for_exit_features", new=_blocking_enricher):
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

    monitor = _monitor_with_exit_model()
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

    with patch("orion.ml.flow_enricher.enrich_flow_for_exit_features", new=_counting_enricher):
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

    monitor = _monitor_with_exit_model()
    monitor._ENTRY_CONTEXT_FETCH_TIMEOUT_SECONDS = 0.01

    async def _slow_enricher(**_kwargs: object) -> dict[str, object]:
        await _blocking_then_yield(0.3)
        return {}

    with patch("orion.ml.flow_enricher.enrich_flow_for_exit_features", new=_slow_enricher):
        await monitor.sync_positions(_connector(symbol))
        # Position closes while its resolution is still running.
        await monitor.sync_positions(SimpleNamespace(get_all_positions=list))
        await _drain(monitor)

    assert symbol not in monitor._entry_context_cache, (
        "a context resolved for a closed position was cached and would be reused by a reopen"
    )
    assert symbol not in monitor._entry_context_applied
    # Per-symbol bookkeeping must not outlive the position that created it.
    assert symbol not in monitor._context_deferred_since
    assert symbol not in monitor._entry_context_retry_at
    assert symbol not in monitor._entry_context_backoff_seconds

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
async def test_no_heber_read_is_attempted_when_no_exit_model_is_loaded() -> None:
    """With no exit model loaded, entry-context enrichment must not touch Heber.

    The four enrichment fields (iv_rank/vix/gex/market_tide) reach exactly one
    consumer: the `ExitFeatures` vector built for `exit_classifier.predict`,
    which `evaluate_exits` only constructs when the position's bucket has a
    trained model. With `models == {}` the scans are pure waste, so they must
    not run at all — while bucket and expiry, which come from the decision row
    and the OCC symbol rather than the enrichment, must be unaffected.
    """
    today = datetime.now(UTC).strftime("%y%m%d")
    symbol = f"SPY{today}C00500000"
    await _insert_orion_decision(symbol, "SPY", dte_days=0)

    monitor = PositionMonitor()
    monitor.exit_classifier.models = {}
    reader = MagicMock()

    with patch("orion.labeler.feature_extraction._heber_reader", reader):
        (pos,) = await monitor.sync_positions(_connector(symbol))
        await _drain(monitor)

    assert reader.mock_calls == [], f"Heber was read with no exit model loaded: {reader.mock_calls[:3]}"
    # The exit-relevant context is unaffected by skipping enrichment: bucket and
    # expiry come from the decision row and the OCC symbol, not the enrichment.
    # (Not asserting an exact expiry date here — the decision row's own
    # expiration is the source when the join resolves, and a 0-DTE row written
    # minutes ago can sit on either side of midnight UTC.)
    assert pos.bucket == "0DTE"
    assert pos.expiry_date is not None


@pytest.mark.asyncio
async def test_enrichment_still_runs_off_loop_when_an_exit_model_is_loaded() -> None:
    """A loaded model must still get its entry-context features, off the loop."""
    option_symbol = "GOOG260117C00200000"
    await _insert_orion_decision(option_symbol, "GOOG")

    monitor = PositionMonitor()
    monitor.exit_classifier.models = {"SWING": object()}
    monitor._ENTRY_CONTEXT_FETCH_TIMEOUT_SECONDS = 0.05
    calls = 0

    async def _blocking_enricher(**_kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        time.sleep(BLOCKING_SECONDS)
        return {"iv_rank_at_entry": 42.5, "vix_at_entry": 18.0}

    ticks = 0
    stop = False

    async def _heartbeat() -> None:
        nonlocal ticks
        while not stop:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            ticks += 1

    beat = asyncio.create_task(_heartbeat())
    try:
        with patch("orion.ml.flow_enricher.enrich_flow_for_exit_features", new=_blocking_enricher):
            await monitor.sync_positions(_connector(option_symbol))
            await _drain(monitor)
    finally:
        stop = True
        beat.cancel()
        with pytest.raises(asyncio.CancelledError):
            await beat

    assert calls == 1, "a loaded model did not get its entry-context enrichment"
    assert monitor._entry_context_cache[option_symbol]["iv_rank_at_entry"] == 42.5
    # Still off the loop.
    expected = int(BLOCKING_SECONDS / HEARTBEAT_INTERVAL) // 2
    assert ticks >= expected, f"loop ticked only {ticks} times (expected >= {expected})"


@pytest.mark.asyncio
async def test_exit_feature_enrichment_never_scans_bars_or_darkpool() -> None:
    """The exit-feature enrichment must not pull the heavy historical datasets.

    `ExitFeatures` carries only iv_rank/vix/gex/market_tide out of the ~60
    fields the full scoring enricher builds. The 370-day `read_bars` behind
    `high_52w_distance_pct` (measured 80s / 7.26GB) is not one of them, so it
    must not run on the exit daemon.
    """
    from orion.ml.flow_enricher import enrich_flow_for_exit_features

    reader = MagicMock()
    reader.read_greek_exposure.return_value = pd.DataFrame()
    reader.read_market_tide.return_value = pd.DataFrame()
    reader.read_iv_rank.return_value = pd.DataFrame()
    reader.read_bars.return_value = pd.DataFrame()
    reader.read_flow.return_value = pd.DataFrame()
    reader.read_darkpool.return_value = pd.DataFrame()
    reader.read_max_pain.return_value = pd.DataFrame()

    with patch("orion.labeler.feature_extraction._heber_reader", reader):
        result = await enrich_flow_for_exit_features(ticker="AAPL", entry_ts=datetime.now(UTC) - timedelta(hours=2))

    assert set(result) == {"iv_rank_at_entry", "vix_at_entry", "gex_at_entry", "market_tide_30m"}
    # The gigabyte-scale silver datasets: bars (4.1 GB) and darkpool (1.2 GB).
    # Nothing ExitFeatures carries needs a long window over either, and max_pain
    # is not an exit feature at all.
    assert reader.read_darkpool.call_count == 0, "darkpool scanned for a feature ExitFeatures does not carry"
    assert reader.read_max_pain.call_count == 0, "max_pain scanned for a feature ExitFeatures does not carry"

    # Every read of a heavy dataset must be bounded to a short window. VIX comes
    # from a 7-day VIXY bars read; the market-tide fallback reads a 30-minute
    # flow window. iv_rank is exempt: a rank needs a year, but its dataset is
    # ~3 MB against 4.1 GB for bars.
    for name in ("read_bars", "read_flow"):
        for call in getattr(reader, name).call_args_list:
            start = call.kwargs.get("start_time")
            asof = call.kwargs.get("asof_time") or datetime.now(UTC)
            assert start is not None, f"unbounded {name} on the exit daemon"
            window = asof - start
            assert window <= timedelta(days=30), f"{name} window {window} is too wide for the exit daemon"


@pytest.mark.asyncio
async def test_enrichment_is_skipped_for_a_bucket_with_no_model_of_its_own() -> None:
    """Adversarial review [high]: the gate must be per-bucket, not "any model".

    `evaluate_exits` consults the classifier only when the position's OWN bucket
    has a model. Enriching because some other bucket has one burns the measured
    1.18 GB lookup on a position that can never use it, and holds the single
    resolver permit ahead of a position whose bucket does have a model.
    """
    today = datetime.now(UTC).strftime("%y%m%d")
    zero_dte = f"SPY{today}C00500000"
    swing = "AAPL260117C00200000"
    await _insert_orion_decision(zero_dte, "SPY", dte_days=0)
    await _insert_orion_decision(swing, "AAPL", dte_days=14)

    monitor = PositionMonitor()
    monitor.exit_classifier = _classifier_with({"0DTE": object()})
    enriched: list[str] = []

    async def _enricher(*, ticker: str, entry_ts: datetime) -> dict[str, object]:
        enriched.append(ticker)
        return {"iv_rank_at_entry": 1.0}

    with patch("orion.ml.flow_enricher.enrich_flow_for_exit_features", new=_enricher):
        await monitor.sync_positions(_connector(swing))
        await _drain(monitor)
        assert enriched == [], "a SWING position enriched when only a 0DTE model is loaded"

        await monitor.sync_positions(_connector(zero_dte))
        await _drain(monitor)

    assert enriched == ["SPY"], "the modeled bucket did not get its enrichment"


@pytest.mark.asyncio
async def test_context_is_re_resolved_when_its_bucket_model_loads_later() -> None:
    """Adversarial review [high]: a skipped enrichment must not be permanent.

    A context resolved while no model existed caches four Nones. If a model for
    that bucket is loaded afterwards, the classifier would start scoring an
    already-open position with its default substitutes instead of the real entry
    features — silently changing ML exit decisions. The skipped context must be
    re-resolved once its bucket has a model.
    """
    symbol = "MSFT260117C00400000"
    await _insert_orion_decision(symbol, "MSFT", dte_days=14)

    monitor = PositionMonitor()
    monitor.exit_classifier = _classifier_with({})

    async def _enricher(*, ticker: str, entry_ts: datetime) -> dict[str, object]:
        return {"iv_rank_at_entry": 42.5, "vix_at_entry": 18.0}

    with patch("orion.ml.flow_enricher.enrich_flow_for_exit_features", new=_enricher):
        (pos,) = await monitor.sync_positions(_connector(symbol))
        await _drain(monitor)
        assert pos.bucket == "SWING"
        assert pos.iv_rank_at_entry is None, "enrichment ran with no model loaded"

        # A SWING model is trained and loaded into the live classifier.
        monitor.exit_classifier.models["SWING"] = object()

        (same_pos,) = await monitor.sync_positions(_connector(symbol))
        await _drain(monitor)

    assert same_pos is pos
    assert pos.iv_rank_at_entry == 42.5, "entry features never resolved after the model loaded"
    assert pos.vix_at_entry == 18.0


@pytest.mark.asyncio
async def test_classifier_is_not_consulted_while_entry_context_is_still_resolving() -> None:
    """Adversarial review [high]: no ML exit on placeholder entry features.

    Resolution runs in the background and can outlast the cycle budget, so a
    position can be evaluated while its entry features are still None. The
    classifier substitutes defaults for those (iv_rank 50, vix 20, gex 0, tide
    0), so consulting it mid-resolution would let an ML exit fire on values the
    position never had. The deterministic barriers stay in force throughout.
    """
    # Expiry ~14 days out and flat P&L, so no deterministic barrier fires and
    # the classifier really is the next step in evaluate_exits — otherwise this
    # test would pass merely because a barrier short-circuited first.
    expiry = (datetime.now(UTC) + timedelta(days=14)).strftime("%y%m%d")
    option_symbol = f"AMZN{expiry}C00200000"
    await _insert_orion_decision(option_symbol, "AMZN", dte_days=14)

    monitor = _monitor_with_exit_model()
    monitor._ENTRY_CONTEXT_FETCH_TIMEOUT_SECONDS = 0.01
    predictions: list[object] = []
    monitor.exit_classifier.predict = lambda features: (  # type: ignore[method-assign]
        predictions.append(features),
        SimpleNamespace(should_exit=False, confidence=0.0, reasoning="stub"),
    )[1]

    async def _slow_enricher(*, ticker: str, entry_ts: datetime) -> dict[str, object]:
        await _blocking_then_yield(0.3)
        return {"iv_rank_at_entry": 42.5, "vix_at_entry": 18.0}

    with patch("orion.ml.flow_enricher.enrich_flow_for_exit_features", new=_slow_enricher):
        # Cycle 1: resolution overruns the budget, so context is not yet applied.
        await monitor.sync_positions(_connector(option_symbol))
        monitor.evaluate_exits()
        assert predictions == [], "classifier consulted while entry context was still resolving"

        await _drain(monitor)

        # Cycle 2: the context has landed, so the model may score it.
        await monitor.sync_positions(_connector(option_symbol))
        monitor.evaluate_exits()

    assert len(predictions) == 1, "classifier never consulted after the context resolved"
    assert predictions[0].iv_rank_at_entry == 42.5  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_classifier_is_withheld_even_when_barrier_evaluation_fails() -> None:
    """Adversarial review [high]: readiness must not be conditional on the barriers.

    When both barrier attempts raise, the classifier is normally the last
    resort so a position is never left with no policy. But if entry context is
    ALSO unresolved, that last resort would score the position on placeholder
    features — acting on fabricated inputs at the worst possible moment. The
    monitor must decline and page instead.
    """
    expiry = (datetime.now(UTC) + timedelta(days=14)).strftime("%y%m%d")
    option_symbol = f"NFLX{expiry}C00200000"
    await _insert_orion_decision(option_symbol, "NFLX", dte_days=14)

    monitor = _monitor_with_exit_model()
    monitor._ENTRY_CONTEXT_FETCH_TIMEOUT_SECONDS = 0.01
    predictions: list[object] = []
    monitor.exit_classifier.predict = lambda features: (  # type: ignore[method-assign]
        predictions.append(features),
        SimpleNamespace(should_exit=True, confidence=1.0, reasoning="stub"),
    )[1]

    async def _slow_enricher(*, ticker: str, entry_ts: datetime) -> dict[str, object]:
        await _blocking_then_yield(0.3)
        return {"iv_rank_at_entry": 42.5}

    with patch("orion.ml.flow_enricher.enrich_flow_for_exit_features", new=_slow_enricher):
        await monitor.sync_positions(_connector(option_symbol))

        # Both barrier attempts raise, so policy_evaluated ends up False.
        with patch(
            "orion.execution.exit_fallback_rules.evaluate_fallback_rules",
            side_effect=RuntimeError("malformed bucket override"),
        ):
            signals = monitor.evaluate_exits()

        await _drain(monitor)

    assert predictions == [], "classifier scored a position on placeholder entry features"
    assert signals == [], "an exit was produced from unresolved context"


@pytest.mark.asyncio
async def test_failed_enrichment_is_not_treated_as_resolved_context() -> None:
    """Adversarial review [high]: a failed lookup must not count as resolved.

    Enrichment failures were caught and turned into an empty payload, which was
    then cached and marked applied — so a Heber outage produced a "ready"
    position whose entry features were all None, and the classifier scored it on
    its defaults. A failure must leave the position unresolved so the classifier
    stays withheld and the retry path keeps trying.
    """
    expiry = (datetime.now(UTC) + timedelta(days=14)).strftime("%y%m%d")
    option_symbol = f"CRM{expiry}C00200000"
    await _insert_orion_decision(option_symbol, "CRM", dte_days=14)

    monitor = _monitor_with_exit_model()
    predictions: list[object] = []
    monitor.exit_classifier.predict = lambda features: (  # type: ignore[method-assign]
        predictions.append(features),
        SimpleNamespace(should_exit=True, confidence=1.0, reasoning="stub"),
    )[1]

    async def _broken_enricher(*, ticker: str, entry_ts: datetime) -> dict[str, object]:
        raise RuntimeError("heber unavailable")

    with patch("orion.ml.flow_enricher.enrich_flow_for_exit_features", new=_broken_enricher):
        await monitor.sync_positions(_connector(option_symbol))
        await _drain(monitor)
        monitor.evaluate_exits()

    assert option_symbol not in monitor._entry_context_cache, "a failed enrichment was cached as resolved"
    assert option_symbol not in monitor._entry_context_applied
    assert predictions == [], "classifier scored a position whose enrichment had failed"


@pytest.mark.asyncio
async def test_exit_feature_gex_does_not_pull_the_rolling_average_window() -> None:
    """Adversarial review [medium]: no 20-day GEX scan for a value we discard.

    The full enricher's GEX wrapper follows a successful snapshot with a
    20-day rolling-average read. The exit payload keeps only the point-in-time
    `gex`, so that second scan is an extra serialized Heber read under the
    single resolver permit for a value nothing consumes.
    """
    import orion.ml.flow_enricher as fe

    rolling = MagicMock()

    async def _snapshot(ticker: str, entry_ts: datetime) -> dict[str, float]:
        return {"gex": 1.5e9, "vex": 2.5e8}

    async def _no_tide(entry_ts: datetime, minutes: int = 30) -> dict[str, object]:
        return {}

    async def _no_iv_rank(ticker: str, entry_ts: datetime) -> None:
        return None

    async def _no_vix(entry_ts: datetime) -> None:
        return None

    # The other three lookups are stubbed so this test stays about GEX and
    # never touches the real Heber cache.
    with (
        patch.object(fe, "get_labeler_gex_at_entry", new=_snapshot),
        patch.object(fe, "get_labeler_gex_rolling_averages", new=rolling),
        patch.object(fe, "_get_market_tide", new=_no_tide),
        patch.object(fe, "_get_iv_rank", new=_no_iv_rank),
        patch.object(fe, "_get_vix", new=_no_vix),
    ):
        result = await fe.enrich_flow_for_exit_features(ticker="AAPL", entry_ts=datetime.now(UTC) - timedelta(hours=2))

    assert result["gex_at_entry"] == 1.5e9
    assert rolling.call_count == 0, "rolling-average GEX window scanned for a discarded value"


@pytest.mark.asyncio
async def test_position_check_complete_reports_process_rss() -> None:
    """Cheap growth guard: the cycle summary must carry process RSS."""
    monitor = PositionMonitor()
    summary = await monitor.run_check(SimpleNamespace(get_all_positions=lambda: []), dry_run=True)

    assert "rss_mb" in summary, "position_check_complete summary is missing rss_mb"
    assert isinstance(summary["rss_mb"], float)
    assert summary["rss_mb"] > 0
