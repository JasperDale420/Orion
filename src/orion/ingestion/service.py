import asyncio
import contextlib
import os
import signal
import traceback
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

from orion.config import system_settings
from orion.clients.heber_reader import get_heber_reader
from orion.connectors.gateway_stream_client import create_gateway_stream_client
from orion.core.health_monitor import CriticalHealthError, HealthMonitor
from orion.core.service_lease import acquire_service_lease, renew_service_lease
from orion.core.timekeeping import derive_trading_date_and_session
from orion.core.universe_manager import UniverseManager
from orion.processing.deduper import DeduplicationEngine
from orion.processing.feature_engine import FeatureEngine
from orion.processing.normalizer import NormalizationEngine
from orion.processing.persistence import (
    persist_bronze_events,
    persist_candidates,
    persist_silver_signals,
)
from orion.processing.rule_engine import RuleEngine
from orion.shared.alerts import send_discord_alert
from orion.shared.db_utils import db_write
from orion.shared.liveness import publish_liveness
from orion.shared.logger import setup_struct_logger
from orion.shared.utils import make_json_safe
from orion.storage.db import async_session_factory, init_db, wait_for_db
from orion.storage.models import BronzeEvent
from orion.storage.models_dlq import DeadLetterQueue
from orion.storage.models_gold import CandidateTrade
from orion.storage.models_silver import SilverSignal

logger = setup_struct_logger("orion.ingest")

# Cycle-latency thresholds. A cycle that runs long means the event loop is
# blocked (sync Heber read, slow DB) and bars/flow are not being drained —
# surface it before it becomes a silent stall.
CYCLE_LATENCY_WARN_SECONDS = 15.0

# Liveness cadence budget: the 60s poll loop should publish well within this
# window; the dead-man watchdog alerts if no successful cycle lands in 300s.
LIVENESS_CADENCE_BUDGET_SECONDS = 300
CYCLE_LATENCY_ERROR_SECONDS = 45.0


class IngestionService:
    def __init__(self) -> None:
        self.run_id: str = system_settings.run_id
        os.environ["ORION_RUN_ID"] = self.run_id

        self.shutdown_event = asyncio.Event()
        self.health_monitor = HealthMonitor()
        self.universe = UniverseManager()
        self.feature_engine = FeatureEngine()
        self.rule_engine = RuleEngine()
        # Gateway stream client for real-time bar data from Data-Gateway
        try:
            self.gateway_stream = create_gateway_stream_client()
            logger.info("GatewayStreamClient created; market data sourced from Data-Gateway")
        except ValueError as e:
            logger.error(f"Failed to create GatewayStreamClient: {e}")
            raise

        # Timezone settings
        self.eastern = ZoneInfo("America/New_York")
        xcals.get_calendar("XNYS")

        # State
        self.eod_trigger_last_run: str | None = None
        self._last_flow_poll_ts: datetime = datetime.now(UTC) - timedelta(
            minutes=system_settings.initial_flow_lookback_minutes
        )
        # Startup hydrate just ran in initialize(); defer the first periodic
        # re-hydration by one interval rather than re-querying immediately.
        self._last_universe_rehydrate_ts: datetime = datetime.now(UTC)
        self._eod_task: asyncio.Task[None] | None = None
        self._rollup_task: asyncio.Task[None] | None = None

        # Gateway WS degrade-mode state. `_ws_ever_connected` guards against
        # tripping DEGRADED during the *initial* connection backoff — we only
        # degrade after a connection was established at least once and then
        # lost. `_ws_degraded` is exposed via `is_degraded` for health/tests.
        self._ws_ever_connected: bool = False
        self._ws_degraded: bool = False

        # Single-instance lease (see orion.core.service_lease). Set in
        # `initialize()` after a successful `acquire_service_lease` call;
        # used by the heartbeat block in `run()` to renew. None until
        # acquired — renewal is then a no-op (defensive against
        # initialize-time failures).
        self._lease_run_id: str | None = None

    async def initialize(self) -> None:
        """Initialize resources that require async execution."""
        logger.info("Initializing Ingestion Service...")

        # Acquire the single-instance lease BEFORE any subscriptions or
        # state-mutating work so a duplicate process refuses to start
        # without producing duplicate bronze events on the way out.
        # `ORION_LEASE_OWNER_ID` differentiates the docker-compose run
        # (`orion_ingestion_compose`) from the native launchd run
        # (`orion_ingestion_native`); identical owner ids would let two
        # processes co-exist, distinct ids mutually exclude. Raises
        # RuntimeError on a fresh competing lease; that propagates so
        # the process exits non-zero and operator sees the failure.
        # Wait out a transient DB outage (bounded) BEFORE init_db + the
        # hydrate_from_db universe load below. A DB-down start previously
        # crash-looped ingestion and left it pinned to the static watchlist for
        # the whole session (2026-06-01 near-outage); waiting for the DB lets
        # the universe hydrate against a live DB instead of a degraded start.
        await wait_for_db()
        await init_db()
        self._lease_run_id = await acquire_service_lease("ingestion")

        if system_settings.reset_circuit_breaker_on_start:
            try:
                from orion.core.circuit_breaker import CircuitBreaker

                await CircuitBreaker().close()
            except Exception as cb_err:
                logger.error(f"Failed to reset circuit breaker on start: {cb_err}", exc_info=True)

        # required=True: a startup hydrate failure must NOT silently fall through
        # to a static-watchlist-only session (2026-06-01 near-outage). Fail loud
        # so the wait_for_db-guarded restart hydrates against a live DB.
        await self.universe.hydrate_from_db(required=True)
        await self.feature_engine.hydrate_history()

        logger.info("Skipping startup earnings sync; earnings data is sourced from Data-Gateway/Heber on demand")

        # Start rollup job as background task
        try:
            from orion.jobs.rollup_job import RollupJob

            rollup_job = RollupJob(loop_interval_seconds=60.0)
            self._rollup_task = asyncio.create_task(rollup_job.run_forever())
            logger.info("Rollup job started as background task")
        except Exception as e:
            logger.error(f"Failed to start rollup job: {e}", exc_info=True)

        # Start Gateway WebSocket stream and subscribe to initial tickers
        try:
            await self.gateway_stream.start()
            # Record that a WS connection was successfully established at least
            # once; degrade-mode detection keys off this so initial-connection
            # backoff is never mistaken for a degraded (post-connect) outage.
            self._ws_ever_connected = True
            initial_tickers = list(system_settings.static_watchlist)
            await self.gateway_stream.subscribe(initial_tickers)
            logger.info(
                "Gateway stream started and subscribed to initial tickers",
                extra={"ticker_count": len(initial_tickers), "tickers": initial_tickers},
            )
        except Exception as e:
            logger.error(f"Failed to start Gateway stream: {e}", exc_info=True)
            raise

        logger.info("Ingestion source profile", extra={"context": self._active_event_source_profile()})
        logger.info("Ingestion Service Initialized.")

    def _handle_shutdown_signals(self) -> None:
        loop = asyncio.get_running_loop()

        def _signal_handler() -> None:
            logger.info("Shutdown signal received. Stopping ingestion loop...")
            self.shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

    async def run(self) -> None:
        await self.initialize()
        self._handle_shutdown_signals()

        logger.info("Starting Polling Loop. Interval: 60s")
        loop_interval = 60.0

        while not self.shutdown_event.is_set():
            start_time = asyncio.get_running_loop().time()
            try:
                await self._run_cycle()
                self._log_cycle_latency(asyncio.get_running_loop().time() - start_time)
            except BaseException as e:
                # Catch BaseException (not just Exception) so SystemExit /
                # CancelledError from a misbehaving background task can't
                # silently break the loop condition. Re-raise after logging
                # if it's a true control-flow signal so shutdown still works.
                logger.error(f"Main Ingestion Loop Error: {type(e).__name__}: {e}", exc_info=True)
                if isinstance(e, KeyboardInterrupt | SystemExit):
                    raise
                if isinstance(e, Exception):
                    await self._persist_loop_crash(e)
                await asyncio.sleep(5.0)

            # Heartbeat & Sleep — wrapped in try/except so a transient DB
            # error here can't punch out of the loop with exit code 0.
            try:
                elapsed = asyncio.get_running_loop().time() - start_time
                sleep_time = max(0.1, loop_interval - elapsed)

                self.health_monitor.update_heartbeat()
                await self._update_health_status()
                # Renew the single-instance lease (no-op if acquisition
                # failed during initialize). Extracted helper so the
                # heartbeat path is directly unit-testable.
                await self._maybe_renew_lease()

                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.shutdown_event.wait(), timeout=sleep_time)
            except Exception as e:
                logger.error(f"Heartbeat/sleep tail error: {type(e).__name__}: {e}", exc_info=True)
                await asyncio.sleep(5.0)

        if self.shutdown_event.is_set():
            await self.stop()
        else:
            # Defensive: while-loop should only exit via shutdown_event.
            # If we land here without the flag set, something silently
            # broke the loop condition — surface as CRITICAL so the
            # operator sees a signal instead of a clean exit-0 in the
            # restart loop log.
            logger.critical(
                "ingestion_main_loop_exited_without_shutdown_signal",
                extra={"event_type": "MAIN_LOOP_UNEXPECTED_EXIT"},
            )
            await self.stop()

    async def stop(self) -> None:
        if self.gateway_stream and self.gateway_stream.is_running:
            await self.gateway_stream.stop()
            logger.info("Gateway stream client stopped")
        # Flush any in-flight background feature persistence before exit so
        # tracked tasks aren't cancelled mid-write on shutdown.
        await self.feature_engine.drain()
        logger.info("Ingestion Service Stopped.")

    async def _maybe_renew_lease(self) -> None:
        """Heartbeat-side lease renewal.

        No-op if `initialize()` never successfully acquired a lease.
        Delegates to the free function in `orion.core.service_lease`,
        which swallows transient DB errors so a blip here can't crash
        the ingestion loop. Repeated failures naturally let the lease
        go stale so another process can legitimately take over.
        """
        if self._lease_run_id is None:
            return
        await renew_service_lease("ingestion", self._lease_run_id)

    async def _run_cycle(self) -> None:
        await self._check_overnight_sleep()
        self.health_monitor.update_heartbeat()

        trace_id = str(uuid.uuid4())

        # Self-heal a sparse/degraded universe before reconciling subscriptions
        # so any recovered tickers get subscribed in the same cycle.
        await self._maybe_rehydrate_universe()

        # Detect/recover a dead Gateway WS stream BEFORE syncing subscriptions
        # or draining events. Degrades (alerts + attempts reconnect) rather than
        # exiting, so the Heber flow-poll path below keeps running regardless.
        await self._check_gateway_stream_health()

        # Sync subscriptions: subscribe to any new tickers discovered by the universe
        await self._sync_gateway_subscriptions()

        # Alarm if subscribed bar breadth has collapsed to ~static-watchlist size
        self._check_universe_breadth()

        # Drain buffered bar events from the Gateway WebSocket stream
        all_events = self.gateway_stream.drain_events()
        for event in all_events:
            self._tag_ingest_metadata(event, trace_id, "gateway_stream")

        # Poll Heber for new UW flow alerts
        flow_events = await self._poll_heber_flow(trace_id)
        all_events.extend(flow_events)

        # Universe updates are deferred to _run_pipeline — only tickers
        # that generate candidates get Gateway bar subscriptions.

        # Process & Persist
        if all_events:
            all_events = await self._normalize_and_dedupe(all_events, trace_id)
            if all_events:
                await self._persist_events(all_events)
                await self._process_features_and_rules(all_events)

        # Process EOD Trigger
        self._check_eod_trigger()

        logger.info(
            "Ingestion heartbeat",
            extra={
                "trace_id": trace_id,
                "context": {
                    "processed_events": len(all_events),
                    "flow_events": len(flow_events),
                },
            },
        )

        # Liveness: one publish per successful cycle (swallows its own errors).
        await publish_liveness("ingestion", cadence_budget_seconds=LIVENESS_CADENCE_BUDGET_SECONDS)

    async def _maybe_rehydrate_universe(self) -> None:
        """Periodically re-hydrate the universe from recent candidate_trades.

        A DB-down/sparse start previously left the WS subscription pinned to the
        static watchlist for the whole session (2026-06-01 near-outage) because
        nothing re-broadened it. Re-running hydrate_from_db on an interval lets a
        degraded start self-correct within minutes — the next
        ``_sync_gateway_subscriptions`` picks up the broadened universe.
        ``required=False`` keeps it non-fatal so a transient DB error here can't
        crash the ingestion loop.
        """
        interval = system_settings.universe_rehydrate_interval_seconds
        if interval <= 0:
            return
        now = datetime.now(UTC)
        if (now - self._last_universe_rehydrate_ts).total_seconds() < interval:
            return
        self._last_universe_rehydrate_ts = now
        await self.universe.hydrate_from_db(required=False)

    def _check_universe_breadth(self) -> None:
        """Alarm when subscribed Alpaca bar breadth collapses during market hours.

        The 2026-06-01 near-outage ran the whole session subscribed to only the
        11 static-watchlist tickers (normal breadth is 100s) and was caught only
        in post-mortem. Log CRITICAL with a structured event_type so it pages at
        the open instead. Only meaningful during market hours, when bars stream.
        """
        from orion.core.market_schedule import MarketSchedule

        if not MarketSchedule().is_market_open():
            return
        threshold = system_settings.universe_breadth_min_tickers
        subscribed = len(self.gateway_stream.subscribed_symbols)
        if subscribed < threshold:
            logger.critical(
                "ingestion_universe_breadth_collapsed",
                extra={
                    "event_type": "UNIVERSE_BREADTH_COLLAPSE",
                    "subscribed_tickers": subscribed,
                    "threshold": threshold,
                },
            )

    async def _sync_gateway_subscriptions(self) -> None:
        """Subscribe to any tickers the universe knows about that the Gateway doesn't."""
        try:
            universe_tickers = set(self.universe.get_active_universe())
            currently_subscribed = self.gateway_stream.subscribed_symbols
            new_tickers = universe_tickers - currently_subscribed
            if new_tickers:
                await self.gateway_stream.subscribe(list(new_tickers))
                logger.info(f"Subscribed to {len(new_tickers)} new tickers via Gateway")
        except Exception as e:
            logger.error(f"Failed to sync Gateway subscriptions: {e}", exc_info=True)

    def _log_cycle_latency(self, elapsed_seconds: float) -> None:
        """Surface a slow ingestion cycle (blocked event loop) before it stalls."""
        if elapsed_seconds >= CYCLE_LATENCY_ERROR_SECONDS:
            logger.error(
                "ingestion_cycle_slow",
                extra={
                    "event_type": "CYCLE_LATENCY_ERROR",
                    "elapsed_seconds": round(elapsed_seconds, 2),
                    "threshold_seconds": CYCLE_LATENCY_ERROR_SECONDS,
                },
            )
        elif elapsed_seconds >= CYCLE_LATENCY_WARN_SECONDS:
            logger.warning(
                "ingestion_cycle_slow",
                extra={
                    "event_type": "CYCLE_LATENCY_WARN",
                    "elapsed_seconds": round(elapsed_seconds, 2),
                    "threshold_seconds": CYCLE_LATENCY_WARN_SECONDS,
                },
            )

    @property
    def is_degraded(self) -> bool:
        """True when bar ingestion is down but the service keeps polling flow."""
        return self._ws_degraded

    async def _check_gateway_stream_health(self) -> None:
        """Detect a dead Gateway WS stream and degrade instead of stalling.

        After MAX_RECONNECT_ATTEMPTS the stream client sets ``is_running``
        False and stops draining bars forever, while the ingestion loop happily
        keeps heartbeating — a classic silent stall. Here we:
          - enter DEGRADED on the first cycle the stream is found dead (only if
            it had connected at least once), firing ONE Discord alert;
          - log ERROR every degraded cycle so the outage stays visible;
          - attempt a fresh reconnect every cycle via the client's restart();
          - on recovery, fire a recovery alert and clear degraded state.
        The Heber flow-poll path in `_run_cycle` continues either way.
        """
        # Never degrade during the initial connection backoff.
        if not self._ws_ever_connected:
            return

        if self.gateway_stream.is_running:
            return

        if not self._ws_degraded:
            self._ws_degraded = True
            await send_discord_alert(
                "Gateway WS bar ingestion is DOWN — reconnect attempts exhausted. "
                "Heber flow polling continues; will keep retrying the WS each cycle.",
                dedupe_key="gateway_ws_down",
            )

        logger.error(
            "gateway_ws_degraded",
            extra={"event_type": "GATEWAY_WS_DEGRADED", "ws_running": False},
        )

        # Attempt to re-establish the WS connection this cycle.
        try:
            recovered = await self.gateway_stream.restart()
        except Exception as e:
            logger.error(f"Gateway stream restart attempt failed: {e}", exc_info=True)
            return

        if recovered:
            self._ws_degraded = False
            await send_discord_alert(
                "Gateway WS bar ingestion RECOVERED — stream reconnected.",
                dedupe_key="gateway_ws_recovered",
            )
            logger.info("gateway_ws_recovered", extra={"event_type": "GATEWAY_WS_RECOVERED"})

    async def _check_overnight_sleep(self) -> None:
        from orion.core.market_schedule import MarketSchedule

        schedule = MarketSchedule()

        # If market is open, don't sleep
        if schedule.is_market_open():
            return

        # Market is closed, calculate sleep
        sleep_seconds = schedule.seconds_until_open()

        if sleep_seconds > 0:
            next_wake = datetime.now(UTC) + timedelta(seconds=sleep_seconds)
            logger.info(f"Market closed. Sleeping until {next_wake} UTC.", extra={"sleep_seconds": sleep_seconds})

            chunk = 60.0
            while sleep_seconds > 0 and not self.shutdown_event.is_set():
                wait = min(chunk, sleep_seconds)
                await asyncio.sleep(wait)
                sleep_seconds -= wait
                self.health_monitor.update_heartbeat()
                # Also refresh the DB-side heartbeat. Without this, the
                # `system_status.global_health` row stays at whatever it was
                # at last market close, and ExecutionEngine._check_system_health
                # treats that staleness as ingestion-dead and blocks every
                # order with "System Status is UNHEALTHY" — even when the
                # in-memory heartbeat above is fine. Refreshing once per
                # 60s sleep chunk keeps the DB row well within
                # ingestion_heartbeat_max_age.
                await self._update_health_status()

    def _active_event_source_profile(self) -> dict[str, str | bool | list[str]]:
        return {
            "data_source": "gateway_stream+heber_flow",
            "gateway_connected": self.gateway_stream.is_running,
            "subscribed_symbols": sorted(self.gateway_stream.subscribed_symbols),
            "produced_event_types": ["ALPACA_BAR_1M", "UW_FLOW"],
            "flow_source": "heber_silver",
        }

    async def _poll_heber_flow(self, trace_id: str) -> list[BronzeEvent]:
        """Poll Heber Silver for new UW flow alerts since last poll.

        The pyarrow read is a blocking call; running it inline on the event
        loop stalls bar draining and heartbeats. Offload it to a worker thread
        with `asyncio.to_thread` (matching the labeler/connectors pattern).
        """
        try:
            reader = get_heber_reader()
            now = datetime.now(UTC)
            # Rewind the read window by the overlap so a recovered Heber outage
            # replays the gap between the pre-outage watermark and now, instead
            # of the watermark jumping straight to `now` and silently dropping
            # the gap. Re-delivered events that were already persisted are
            # filtered out by the DeduplicationEngine (and bronze ON CONFLICT);
            # the born-stale drop below still discards anything past the
            # data-lag budget, so the overlap only resurfaces recent unseen
            # events and cannot resurrect the "born-stale SKIP" incident class.
            overlap = timedelta(seconds=system_settings.flow_poll_overlap_seconds)
            read_start = self._last_flow_poll_ts - overlap
            df = await asyncio.to_thread(
                reader.read_flow,
                symbols=None,
                asof_time=now,
                start_time=read_start,
            )

            if df.empty:
                return []

            self._last_flow_poll_ts = now
            events: list[BronzeEvent] = []
            stale_dropped = 0
            # Flow alerts already past the data-lag budget at ingest become
            # candidates that the execution path is guaranteed to reject
            # (preflight Data-Lag gate / auto_skip_stale_candidates). Dropping
            # them here — rather than minting doomed candidates — is what
            # suppresses the startup catch-up burst of prior-day events that
            # otherwise manufactures thousands of born-stale "Stale at fetch"
            # SKIPs. The downstream cutoff is computed against a later `now`, so
            # anything dropped here is strictly staler at decision time too:
            # this never removes an event the execution path would have kept.
            freshness_cutoff = now - timedelta(seconds=system_settings.max_data_lag_seconds)

            for _, row in df.iterrows():
                event = self._heber_row_to_event(row, now)
                if not event:
                    continue
                event_ts = event.event_ts_utc
                if event_ts is not None and event_ts.tzinfo is None:
                    event_ts = event_ts.replace(tzinfo=UTC)
                if event_ts is not None and event_ts < freshness_cutoff:
                    stale_dropped += 1
                    continue
                self._tag_ingest_metadata(event, trace_id, "heber_flow")
                events.append(event)

            if stale_dropped:
                logger.info(
                    f"Dropped {stale_dropped} stale UW flow alerts at ingest "
                    f"(older than {system_settings.max_data_lag_seconds}s data-lag budget)",
                    extra={"stale_dropped": stale_dropped, "fresh_kept": len(events)},
                )

            if events:
                logger.info(
                    f"Polled {len(events)} UW flow alerts from Heber",
                    extra={"flow_count": len(events)},
                )

            return events

        except Exception as e:
            logger.error(f"Heber flow poll failed: {e}", exc_info=True)
            return []

    @staticmethod
    def _make_json_safe(value: Any) -> Any:
        """Convert Parquet-native types and NaN/Inf to JSON-serializable Python types."""
        return make_json_safe(value)

    @staticmethod
    def _heber_row_to_event(row: Any, now: datetime) -> BronzeEvent | None:
        """Convert a Heber Silver flow_alerts row to a BronzeEvent.

        Source is set to "UW" because the data originates from Unusual Whales.
        Heber is the storage layer; provenance is tracked via ingest.connector.
        """
        raw = row.to_dict()
        # Convert Parquet-native types (Timestamp, numpy int/float, NaT) to JSON-safe values
        payload = {
            k: IngestionService._make_json_safe(v)
            for k, v in raw.items()
            if v is not None and not (hasattr(v, "__class__") and v.__class__.__name__ == "NaTType")
        }

        ticker = str(payload.get("underlying") or payload.get("symbol") or "")
        if not ticker:
            return None

        payload["ticker"] = ticker
        if "put_call" in payload:
            pc = str(payload["put_call"]).upper()
            payload["put_call"] = pc[0] if pc else ""
        if "premium" in payload:
            payload["premium_usd"] = float(payload["premium"])
        if "expiry" in payload:
            payload["expiry"] = str(payload["expiry"])
            dte = IngestionService._compute_dte(payload["expiry"], now)
            if dte is not None:
                payload["dte"] = dte
        payload["aggressor_ind"] = IngestionService._infer_aggressor(payload)

        raw_event_id = payload.get("event_id")
        if raw_event_id:
            event_id = str(raw_event_id)
        else:
            # Deterministic fallback: the watermark overlap window re-reads
            # recent rows on every poll, and dedup keys on event_id. A random
            # uuid4 here minted a NEW id for the same id-less row each poll,
            # defeating dedup and duplicating bronze/silver work. Hash stable
            # row fields instead so re-reads collapse to one event.
            basis = "|".join(
                str(payload.get(k, ""))
                for k in ("ticker", "ts_event", "executed_at", "premium", "put_call", "strike", "expiry", "volume")
            )
            event_id = f"uwflow_{hashlib.sha1(basis.encode()).hexdigest()}"

        # Extract event timestamp before it was converted to ISO string
        ts_event_raw = raw.get("ts_event")
        if hasattr(ts_event_raw, "to_pydatetime"):
            ts_event = ts_event_raw.to_pydatetime()
        elif isinstance(ts_event_raw, datetime):
            ts_event = ts_event_raw
        else:
            ts_event = now

        return BronzeEvent(
            event_id=event_id,
            source="UW",
            event_type="UW_FLOW",
            event_ts_utc=ts_event,
            received_ts_utc=now,
            ticker=ticker,
            payload=payload,
        )

    @staticmethod
    def _compute_dte(expiry_str: str, now: datetime) -> int | None:
        """Compute days-to-expiry from expiry string."""
        try:
            from datetime import date as _date

            exp = _date.fromisoformat(expiry_str[:10])
            return max(0, (exp - now.date()).days)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _infer_aggressor(payload: dict) -> str:
        """Infer aggressor from raw field or ask/bid side premium."""
        raw = payload.get("aggressor")
        if raw:
            return str(raw).upper()
        ask = float(payload.get("total_ask_side_prem") or 0)
        bid = float(payload.get("total_bid_side_prem") or 0)
        if ask > bid and ask > 0:
            return "ASK"
        if bid > ask and bid > 0:
            return "BID"
        return "MID"

    async def _normalize_and_dedupe(self, events: list[BronzeEvent], trace_id: str) -> list[BronzeEvent]:
        normalized = []
        for e in events:
            try:
                e.payload = NormalizationEngine.normalize_event(e.source, e.event_type, e.payload)
                # Scrub NaN/Inf values introduced by normalizer float() casts
                e.payload = IngestionService._make_json_safe(e.payload)
                self._enrich_temporal_data(e)
                if not e.ticker:
                    payload_ticker = e.payload.get("ticker")
                    if isinstance(payload_ticker, str):
                        e.ticker = payload_ticker
                self._tag_ingest_metadata(e, trace_id, "unknown", force_defaults=True)
                normalized.append(e)
            except Exception as e_norm:
                await self._send_to_dlq(e_norm, "NORMALIZE_ERROR", e, trace_id)

        async with async_session_factory() as session:
            deduper = DeduplicationEngine(session)
            return await deduper.dedupe_batch(normalized)

    async def _persist_events(self, events: list[BronzeEvent]) -> None:
        await self._save_events_to_db(events)
        # Silver materialization is a no-op (Heber is canonical Silver source);
        # skip to avoid wasting a DB session each cycle.

    async def _process_features_and_rules(self, events: list[BronzeEvent]) -> None:
        try:
            self.feature_engine.process_uw_flow(events)
        except Exception as e:
            logger.error(f"Feature Engine State Update Error: {e}")

        # UW Flow Rules
        uw_flow = [e for e in events if e.event_type == "UW_FLOW"]
        if uw_flow:
            await self._run_pipeline(uw_flow, self.feature_engine.process_uw_flow_events, "UW")

        # Alpaca Rules
        alpaca_bars = [e for e in events if e.event_type == "ALPACA_BAR_1M"]
        if alpaca_bars:
            await self._run_pipeline(alpaca_bars, self.feature_engine.process_alpaca_bars, "Alpaca")

    async def _run_pipeline(self, events: list[BronzeEvent], feature_fn: Any, label: str) -> None:
        try:
            signals = feature_fn(events)
            if signals:
                await self._save_signals(signals)
                candidates = self.rule_engine.process_signals(signals)
                if candidates:
                    await self._save_candidates(candidates)
                    # Subscribe to bar data only for tickers that generated candidates
                    for c in candidates:
                        self.universe.add_ticker(c.ticker)
        except Exception as e:
            # Mirror _persist_events: don't just log-and-drop the batch — route
            # the failing events to the DLQ so the failure is recoverable and
            # visible, then continue the cycle.
            logger.error(f"{label} Pipeline Error: {e}")
            event_ids = [getattr(ev, "event_id", None) for ev in events]
            await self._send_to_dlq(
                e,
                f"{label}_PIPELINE_ERROR",
                payload={"event_ids": event_ids, "event_count": len(events)},
            )

    def _check_eod_trigger(self) -> None:
        now_utc = datetime.now(UTC)
        if now_utc.hour == 1 and now_utc.minute >= 5:
            today_str = now_utc.date().isoformat()
            if self.eod_trigger_last_run != today_str:
                logger.info("Triggering EOD Review Agent...")
                # Save task to prevent garbage collection
                self._eod_task = asyncio.create_task(self._run_eod_task())
                self.eod_trigger_last_run = today_str

    @staticmethod
    async def _run_eod_task() -> None:
        try:
            from orion.agents.eod_review_agent import EODReviewAgent

            agent = EODReviewAgent()
            await agent.run_review()
        except Exception as e:
            logger.error(f"EOD Agent Failed: {e}")

    # --- Helpers ---

    def _enrich_temporal_data(self, e: BronzeEvent) -> None:
        if e.event_ts_utc and e.session is None:
            if e.event_ts_utc.tzinfo is None:
                e.event_ts_utc = e.event_ts_utc.replace(tzinfo=UTC)
            td, sess = derive_trading_date_and_session(e.event_ts_utc)
            e.trading_date = td
            e.session = sess

        if e.event_ts_utc and e.trading_date is None:
            td, _ = derive_trading_date_and_session(e.event_ts_utc)
            e.trading_date = td

        if e.session is None:
            e.session = "CLOSED"

        if e.received_ts_utc is None:
            e.received_ts_utc = datetime.now(UTC)

    def _tag_ingest_metadata(self, e: BronzeEvent, trace_id: str, connector: str, force_defaults: bool = False) -> None:
        if not getattr(e, "ingest", None):
            e.ingest = {
                "connector": connector,
                "run_id": self.run_id,
                "trace_id": trace_id,
                "attempt": 1,
            }
        elif force_defaults:
            ingest_dict: dict[str, Any] = e.ingest if isinstance(e.ingest, dict) else {}
            ingest_dict.setdefault("run_id", self.run_id)
            ingest_dict.setdefault("trace_id", trace_id)
            ingest_dict.setdefault("attempt", 1)
            e.ingest = ingest_dict

    async def _check_lag(self, e: BronzeEvent) -> None:
        if e.event_ts_utc:
            with contextlib.suppress(CriticalHealthError):
                await self.health_monitor.check_lag(e.event_ts_utc)

    async def _update_health_status(self) -> None:
        try:
            await self.health_monitor.check_health()
            await self.health_monitor.update_db_status(True, "Nominal")
        except CriticalHealthError as che:
            logger.critical(f"HEALTH MONITOR FAILURE: {che}")
            await self.health_monitor.update_db_status(False, str(che))

    # --- Persistence Wrappers ---

    async def _save_events_to_db(self, events: list[BronzeEvent]) -> None:
        async def persist_bronze(session: Any) -> None:
            try:
                await persist_bronze_events(session, events)
                logger.info(f"Saved {len(events)} events to DB.")
            except Exception as e:
                logger.error(f"DB Write Error: {e}")
                raise

        await db_write(persist_bronze)

    async def _save_signals(self, signals: list[SilverSignal]) -> None:
        async def persist_signals_op(session: Any) -> None:
            try:
                await persist_silver_signals(session, signals)
                logger.info(f"Saved {len(signals)} signals to DB.")
            except Exception as e:
                logger.error(f"Signal Write Error: {e}")
                raise

        await db_write(persist_signals_op)

    async def _save_candidates(self, candidates: list[CandidateTrade]) -> None:
        async def persist_candidates_op(session: Any) -> None:
            try:
                await persist_candidates(session, candidates)
                logger.info(f"Saved {len(candidates)} candidates to DB.")
            except Exception as e:
                logger.error(f"Candidate Write Error: {e}")
                raise

        await db_write(persist_candidates_op)

    async def _send_to_dlq(
        self,
        error: Exception,
        event_type: str,
        payload: Any = None,
        trace_id: str | None = None,
        source: str = "IngestionService",
    ) -> None:
        try:
            from orion.shared.dlq_utils import DLQWriter

            # Simplify payload for DLQ if it's an object
            pl: dict[str, Any] | str | None = None

            if isinstance(payload, dict):
                # safely cast assume keys are strings or convert
                pl = {str(k): v for k, v in payload.items()}
            elif isinstance(payload, str):
                pl = payload
            elif payload is None:
                pl = None
            else:
                # Lists, objects, etc -> stringify
                pl = str(payload)

            await DLQWriter.write_to_dlq(
                error=error, event_type=event_type, source=source, payload=pl, run_id=self.run_id, trace_id=trace_id
            )
        except Exception as e:
            logger.critical(f"DLQ Failure: {e}")

    async def _persist_loop_crash(self, e: Exception) -> None:
        async def persist_crash_op(session: Any) -> None:
            session.add(
                DeadLetterQueue(
                    error_message=str(e),
                    stack_trace=traceback.format_exc(),
                    source="INGEST_LOOP_CRASH",
                    event_type="CRITICAL",
                    payload={"run_id": self.run_id},
                )
            )

        await db_write(persist_crash_op)
