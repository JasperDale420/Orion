import asyncio
import os
import signal
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union

import exchange_calendars as xcals
import pytz

from orion.config import system_settings
from orion.connectors.alpaca_market_connector import AlpacaMarketConnector
from orion.connectors.redpanda_producer import RedpandaProducer
from orion.connectors.uw_alerts_connector import UWAlertsConnector
from orion.connectors.uw_darkpool_connector import UWDarkPoolConnector
from orion.connectors.uw_flow_connector import UWFlowConnector
from orion.core.health_monitor import CriticalHealthException, HealthMonitor
from orion.core.timekeeping import derive_trading_date_and_session
from orion.core.universe_manager import UniverseManager
from orion.processing.deduper import DeduplicationEngine
from orion.processing.feature_engine import FeatureEngine
from orion.processing.normalizer import NormalizationEngine
from orion.processing.persistence import (
    persist_bronze_events,
    persist_candidates,
    persist_silver_from_bronze,
    persist_silver_signals,
)
from orion.processing.rule_engine import RuleEngine
from orion.shared.db_utils import db_write
from orion.shared.logger import setup_struct_logger
from orion.storage.db import async_session_factory, init_db
from orion.storage.lakehouse import LakehouseWriter
from orion.storage.models import BronzeEvent
from orion.storage.models_dlq import DeadLetterQueue
from orion.storage.models_gold import CandidateTrade
from orion.storage.models_silver import SilverSignal

logger = setup_struct_logger("orion.ingest")


class IngestionService:
    def __init__(self) -> None:
        self.run_id: str = os.getenv("ORION_RUN_ID") or str(uuid.uuid4())
        os.environ["ORION_RUN_ID"] = self.run_id

        self.shutdown_event = asyncio.Event()
        self.health_monitor = HealthMonitor()
        self.universe = UniverseManager()
        self.feature_engine = FeatureEngine()
        self.rule_engine = RuleEngine()
        self.lakehouse = LakehouseWriter()

        # Connectors
        uw_base_url = os.getenv("UW_BASE_URL", "https://api.unusualwhales.com/api")

        # Ensure keys are strings
        uw_key = system_settings.uw_api_key or ""
        alpaca_key = system_settings.alpaca_api_key or ""
        alpaca_secret = system_settings.alpaca_secret_key or ""

        self.uw_flow = UWFlowConnector(api_key=uw_key)
        self.uw_dark = UWDarkPoolConnector(api_key=uw_key, base_url=uw_base_url)
        self.uw_alerts = UWAlertsConnector(api_key=uw_key, base_url=uw_base_url)

        self.alpaca = AlpacaMarketConnector(
            api_key=alpaca_key,
            secret_key=alpaca_secret,
            paper=system_settings.alpaca_paper,
        )

        self.producer: RedpandaProducer | None = None

        # Timezone settings
        self.eastern = pytz.timezone("America/New_York")
        xcals.get_calendar("XNYS")

        # State
        self.eod_trigger_last_run: Optional[str] = None
        self._eod_task: Optional[asyncio.Task[None]] = None

    async def initialize(self) -> None:
        """Initialize resources that require async execution."""
        logger.info("Initializing Ingestion Service...")
        self.producer = await RedpandaProducer.get_instance()
        await self.producer.start()

        if os.getenv("ORION_RESET_CIRCUIT_BREAKER_ON_START", "false").lower() == "true":
            try:
                from orion.core.circuit_breaker import CircuitBreaker

                await CircuitBreaker().close()
            except Exception as cb_err:
                logger.warning(f"Failed to reset circuit breaker on start: {cb_err}")

        await init_db()
        await self.universe.hydrate_from_db()

        # Sync today's earnings calendar from UW API
        try:
            from orion.jobs.sync_earnings import sync_todays_earnings

            result = await sync_todays_earnings()
            logger.info(f"Earnings calendar synced: {result}")
        except Exception as e:
            logger.warning(f"Failed to sync earnings calendar on startup: {e}")

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

            await self._update_health_status()

            try:
                await asyncio.wait_for(self.shutdown_event.wait(), timeout=sleep_time)
            except asyncio.TimeoutError:
                pass

        await self.stop()

    async def stop(self) -> None:
        if self.producer:
            await self.producer.stop()
        logger.info("Ingestion Service Stopped.")

    async def _run_cycle(self) -> None:
        await self._check_overnight_sleep()
        self.health_monitor.update_heartbeat()

        trace_id = str(uuid.uuid4())
        all_events: List[BronzeEvent] = []

        # 1. Poll UW
        events = await self._poll_uw(trace_id)
        all_events.extend(events)

        # 2. Poll Alpaca
        active_tickers = self.universe.get_active_universe()
        if active_tickers:
            alpaca_events = await self._poll_alpaca(active_tickers, trace_id)
            all_events.extend(alpaca_events)

        # 3. Process & Persist
        if all_events:
            all_events = await self._normalize_and_dedupe(all_events, trace_id)
            if all_events:
                await self._persist_events(all_events)
                await self._process_features_and_rules(all_events)
                await self._write_to_lakehouse(all_events, trace_id)

        # 4. Process EOD Trigger
        self._check_eod_trigger()

        logger.info(
            "Ingestion heartbeat", extra={"trace_id": trace_id, "context": {"processed_events": len(all_events)}}
        )

    async def _check_overnight_sleep(self) -> None:
        from orion.core.market_schedule import MarketSchedule

        schedule = MarketSchedule()

        # If market is open, don't sleep
        if schedule.is_market_open():
            return

        # Market is closed, calculate sleep
        sleep_seconds = schedule.seconds_until_open()

        if sleep_seconds > 0:
            next_wake = datetime.now(timezone.utc) + timedelta(seconds=sleep_seconds)
            logger.info(f"Market closed. Sleeping until {next_wake} UTC.", extra={"sleep_seconds": sleep_seconds})

            chunk = 60.0
            while sleep_seconds > 0 and not self.shutdown_event.is_set():
                wait = min(chunk, sleep_seconds)
                await asyncio.sleep(wait)
                sleep_seconds -= wait
                self.health_monitor.update_heartbeat()

    async def _poll_uw(self, trace_id: str) -> List[BronzeEvent]:
        try:
            # lookback_seconds only applies on cold start (no watermark); after first poll, watermarks take over
            flow = await self.uw_flow.poll(lookback_seconds=300)
            dark = await self.uw_dark.fetch_events(lookback_seconds=300)
            alert = await self.uw_alerts.fetch_events(lookback_seconds=300)
            events = flow + dark + alert

            for e in events:
                await self._check_lag(e)
                self._tag_ingest_metadata(e, trace_id, e.event_type.lower())
                e_ticker = e.ticker
                if e_ticker:
                    self.universe.update_from_event(e)

            return events
        except Exception as e:
            logger.error(f"Error polling UW: {e}", extra={"trace_id": trace_id})
            return []

    async def _poll_alpaca(self, tickers: List[str], trace_id: str) -> List[BronzeEvent]:
        try:
            events = self.alpaca.poll(tickers, default_lookback_minutes=system_settings.alpaca_lookback_minutes)
            if events:
                newest = max((e.event_ts_utc for e in events if e.event_ts_utc), default=None)
                if newest:
                    await self.health_monitor.check_lag(newest)

            for e in events:
                self._tag_ingest_metadata(e, trace_id, "alpaca_market")
            return events
        except Exception as e:
            logger.error(f"Error polling Alpaca: {e}", extra={"trace_id": trace_id})
            return []

    async def _normalize_and_dedupe(self, events: List[BronzeEvent], trace_id: str) -> List[BronzeEvent]:
        normalized = []
        for e in events:
            try:
                e.payload = NormalizationEngine.normalize_event(e.source, e.event_type, e.payload)
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

    async def _persist_events(self, events: List[BronzeEvent]) -> None:
        await self._save_events_to_db(events)
        await self._save_silver_data(events)

    async def _process_features_and_rules(self, events: List[BronzeEvent]) -> None:
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

    async def _run_pipeline(self, events: List[BronzeEvent], feature_fn: Any, label: str) -> None:
        try:
            signals = feature_fn(events)
            if signals:
                await self._save_signals(signals)
                candidates = self.rule_engine.process_signals(signals)
                if candidates:
                    await self._save_candidates(candidates)
        except Exception as e:
            logger.error(f"{label} Pipeline Error: {e}")

    async def _write_to_lakehouse(self, events: List[BronzeEvent], trace_id: str) -> None:
        try:
            self.lakehouse.write_events(events)
        except Exception as e:
            logger.error(f"Lakehouse Write Error: {e}")
            await self._send_to_dlq(e, "LAKE_WRITE_FAILED", payload={"count": len(events)}, trace_id=trace_id)

    def _check_eod_trigger(self) -> None:
        now_utc = datetime.now(timezone.utc)
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
                e.event_ts_utc = e.event_ts_utc.replace(tzinfo=timezone.utc)
            td, sess = derive_trading_date_and_session(e.event_ts_utc)
            e.trading_date = td
            e.session = sess

        if e.event_ts_utc and e.trading_date is None:
            td, _ = derive_trading_date_and_session(e.event_ts_utc)
            e.trading_date = td

        if e.session is None:
            e.session = "CLOSED"

        if e.received_ts_utc is None:
            e.received_ts_utc = datetime.now(timezone.utc)

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
            try:
                await self.health_monitor.check_lag(e.event_ts_utc)
            except CriticalHealthException:
                pass  # Logged by monitor or ignored as per policy

    async def _update_health_status(self) -> None:
        try:
            await self.health_monitor.check_health()
            await self.health_monitor.update_db_status(True, "Nominal")
        except CriticalHealthException as che:
            logger.critical(f"HEALTH MONITOR FAILURE: {che}")
            await self.health_monitor.update_db_status(False, str(che))

    # --- Persistence Wrappers ---

    async def _save_events_to_db(self, events: List[BronzeEvent]) -> None:
        # Redpanda Produce
        for e in events:
            try:
                key = e.ticker if e.ticker else e.event_id
                payload = self._to_dict(e)
                if not self.producer:
                    self.producer = await RedpandaProducer.get_instance()
                await self.producer.produce_event(topic="orion.events.bronze", key=key, payload=payload)
            except Exception as e_prod:
                logger.error(f"Redpanda Produce Failed: {e_prod}")
                # Add DLQ logic here if needed, omitted for brevity as DB write is primary

        async def persist_bronze(session: Any) -> None:
            try:
                await persist_bronze_events(session, events)
                logger.info(f"Saved {len(events)} events to DB.")
            except Exception as e:
                logger.error(f"DB Write Error: {e}")
                raise

        await db_write(persist_bronze)

    async def _save_silver_data(self, events: List[BronzeEvent]) -> None:
        async def persist_silver(session: Any) -> None:
            try:
                await persist_silver_from_bronze(session, events)
            except Exception as e:
                logger.error(f"Silver Write Error: {e}")
                raise

        await db_write(persist_silver)

    async def _save_signals(self, signals: List[SilverSignal]) -> None:
        async def persist_signals_op(session: Any) -> None:
            try:
                await persist_silver_signals(session, signals)
                logger.info(f"Saved {len(signals)} signals to DB.")
            except Exception as e:
                logger.error(f"Signal Write Error: {e}")
                raise

        await db_write(persist_signals_op)

    async def _save_candidates(self, candidates: List[CandidateTrade]) -> None:
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
        trace_id: Optional[str] = None,
        source: str = "IngestionService",
    ) -> None:
        try:
            from orion.shared.dlq_utils import DLQWriter

            # Simplify payload for DLQ if it's an object
            pl: Optional[Union[Dict[str, Any], str]] = None

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

    def _to_dict(self, e: BronzeEvent) -> dict[str, Any]:
        return {
            "event_id": e.event_id,
            "source": e.source,
            "event_type": e.event_type,
            "ticker": e.ticker,
            "event_ts_utc": e.event_ts_utc.isoformat() if e.event_ts_utc else None,
            "payload": e.payload,
        }
