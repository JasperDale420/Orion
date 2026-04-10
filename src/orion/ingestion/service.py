import asyncio
import contextlib
import os
import signal
import traceback
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

from orion.config import system_settings
from orion.clients.heber_reader import get_heber_reader
from orion.connectors.gateway_stream_client import create_gateway_stream_client
from orion.core.health_monitor import CriticalHealthError, HealthMonitor
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
from orion.shared.db_utils import db_write
from orion.shared.logger import setup_struct_logger
from orion.shared.utils import make_json_safe
from orion.storage.db import async_session_factory, init_db
from orion.storage.models import BronzeEvent
from orion.storage.models_dlq import DeadLetterQueue
from orion.storage.models_gold import CandidateTrade
from orion.storage.models_silver import SilverSignal

logger = setup_struct_logger("orion.ingest")


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
        self._last_flow_poll_ts: datetime = datetime.now(UTC) - timedelta(minutes=15)
        self._eod_task: asyncio.Task[None] | None = None
        self._rollup_task: asyncio.Task[None] | None = None

    async def initialize(self) -> None:
        """Initialize resources that require async execution."""
        logger.info("Initializing Ingestion Service...")

        if system_settings.reset_circuit_breaker_on_start:
            try:
                from orion.core.circuit_breaker import CircuitBreaker

                await CircuitBreaker().close()
            except Exception as cb_err:
                logger.error(f"Failed to reset circuit breaker on start: {cb_err}", exc_info=True)

        await init_db()
        await self.universe.hydrate_from_db()
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
            except Exception as e:
                logger.error(f"Main Ingestion Loop Error: {e}")
                await self._persist_loop_crash(e)
                await asyncio.sleep(5.0)

            # Heartbeat & Sleep
            elapsed = asyncio.get_running_loop().time() - start_time
            sleep_time = max(0.1, loop_interval - elapsed)

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.shutdown_event.wait(), timeout=sleep_time)

            # Update heartbeat after sleep so check_health sees a fresh timestamp
            self.health_monitor.update_heartbeat()
            await self._update_health_status()

        await self.stop()

    async def stop(self) -> None:
        if self.gateway_stream and self.gateway_stream.is_running:
            await self.gateway_stream.stop()
            logger.info("Gateway stream client stopped")
        logger.info("Ingestion Service Stopped.")

    async def _run_cycle(self) -> None:
        await self._check_overnight_sleep()
        self.health_monitor.update_heartbeat()

        trace_id = str(uuid.uuid4())

        # Sync subscriptions: subscribe to any new tickers discovered by the universe
        await self._sync_gateway_subscriptions()

        # Drain buffered bar events from the Gateway WebSocket stream
        all_events = self.gateway_stream.drain_events()
        for event in all_events:
            self._tag_ingest_metadata(event, trace_id, "gateway_stream")

        # Poll Heber for new UW flow alerts
        flow_events = self._poll_heber_flow(trace_id)
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

            chunk = 45.0  # Must be < HEARTBEAT_THRESHOLD_SEC (60s) to avoid false alerts
            while sleep_seconds > 0 and not self.shutdown_event.is_set():
                wait = min(chunk, sleep_seconds)
                await asyncio.sleep(wait)
                sleep_seconds -= wait
                self.health_monitor.update_heartbeat()

    def _active_event_source_profile(self) -> dict[str, str | bool | list[str]]:
        return {
            "data_source": "gateway_stream+heber_flow",
            "gateway_connected": self.gateway_stream.is_running,
            "subscribed_symbols": sorted(self.gateway_stream.subscribed_symbols),
            "produced_event_types": ["ALPACA_BAR_1M", "UW_FLOW"],
            "flow_source": "heber_silver",
        }

    def _poll_heber_flow(self, trace_id: str) -> list[BronzeEvent]:
        """Poll Heber Silver for new UW flow alerts since last poll."""
        try:
            reader = get_heber_reader()
            now = datetime.now(UTC)
            df = reader.read_flow(
                symbols=None,
                asof_time=now,
                start_time=self._last_flow_poll_ts,
            )

            if df.empty:
                return []

            self._last_flow_poll_ts = now
            events: list[BronzeEvent] = []

            for _, row in df.iterrows():
                event = self._heber_row_to_event(row, now)
                if event:
                    self._tag_ingest_metadata(event, trace_id, "heber_flow")
                    events.append(event)

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

        event_id = str(payload.get("event_id") or uuid.uuid4())

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
            logger.error(f"{label} Pipeline Error: {e}")

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
