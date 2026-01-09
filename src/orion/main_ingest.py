import asyncio
import os
import signal
from datetime import datetime, timedelta, timezone
from typing import Any, List

import exchange_calendars as xcals
from dotenv import load_dotenv

# Load env before importing local modules that might read env at module level
load_dotenv()

import traceback
import uuid

from orion.config import system_settings
from orion.connectors.alpaca_market_connector import AlpacaMarketConnector
from orion.connectors.uw_alerts_connector import UWAlertsConnector
from orion.connectors.uw_darkpool_connector import UWDarkPoolConnector
from orion.connectors.uw_flow_connector import UWFlowConnector
from orion.core.universe_manager import UniverseManager
from orion.processing.deduper import DeduplicationEngine
from orion.processing.feature_engine import FeatureEngine
from orion.processing.persistence import (
    persist_bronze_events,
    persist_candidates,
    persist_silver_from_bronze,
    persist_silver_signals,
)
from orion.processing.rule_engine import RuleEngine
from orion.shared.db_utils import db_write
from orion.shared.logger import setup_struct_logger
from orion.shared.utils import ensure_utc
from orion.storage.db import async_session_factory, init_db
from orion.storage.lakehouse import LakehouseWriter
from orion.storage.models import BronzeEvent
from orion.storage.models_dlq import DeadLetterQueue
from orion.storage.models_gold import CandidateTrade
from orion.storage.models_silver import SilverSignal

# Configure logging
# logging.basicConfig(...) # Removed in favor of struct logger
RUN_ID = os.getenv("ORION_RUN_ID") or str(uuid.uuid4())
os.environ["ORION_RUN_ID"] = RUN_ID
logger = setup_struct_logger("orion.ingest")

# Initialize metrics
try:
    from orion.shared.metrics import Metrics, init_metrics

    _metrics: Metrics | None = None
except ImportError:
    _metrics = None

load_dotenv()

# Global flag for EOD tracking
# SHUTDOWN removed in favor of asyncio.Event in main()
EOD_TRIGGER_LAST_RUN = None
QUALITY_CHECK_LOOP_COUNT = 0  # Track loop iterations for hourly quality check


from orion.connectors.redpanda_producer import RedpandaProducer
from orion.shared.decorators import db_retry


@db_retry
async def save_events_to_db(events: List[BronzeEvent]) -> None:
    if not events:
        return

    # Dual-write: Produce to Redpanda
    producer = await RedpandaProducer.get_instance()

    # print(f"DEBUG: Skipping Redpanda, proceeding to DB save for {len(events)} events.")
    for e in events:
        try:
            # Construct a clean dict for JSON serialization
            payload_dict = {
                "event_id": e.event_id,
                "source": e.source,
                "source_event_id": getattr(e, "source_event_id", None),
                "event_type": e.event_type,
                "event_ts_utc": (
                    e.event_ts_utc.isoformat() if hasattr(e.event_ts_utc, "isoformat") else str(e.event_ts_utc)
                ),
                "received_ts_utc": (
                    e.received_ts_utc.isoformat() if hasattr(e.received_ts_utc, "isoformat") else str(e.received_ts_utc)
                ),
                "payload": e.payload,
                "ticker": e.ticker,
                "trading_date": str(e.trading_date),
                "session": e.session,
                "schema_version": e.schema_version,
                "ingest": getattr(e, "ingest", None),
            }
            # Key by ticker for strict ordering if needed, or event_id
            key = e.ticker if e.ticker else e.event_id

            # Robust Produce with internal retries
            await producer.produce_event(topic="orion.events.bronze", key=key, payload=payload_dict)
        except Exception as prod_err:
            # Fallback to DLQ if Redpanda fails after retries
            logger.error(f"Redpanda Produce Failed (DLQ Fallback): {prod_err}")
            try:
                from orion.shared.dlq_utils import DLQWriter

                await DLQWriter.write_to_dlq(
                    error=prod_err,
                    event_type="REDPANDA_PRODUCE_FAILED",
                    source="RedpandaProducer",
                    payload=payload_dict,
                    context=f"Failed to produce event_id={e.event_id} to topic=orion.events.bronze",
                    run_id=RUN_ID,
                    event_id=e.event_id,
                    ticker=e.ticker,
                    event_ts_utc=e.event_ts_utc,
                )
            except Exception as dlq_err:
                logger.critical(f"FATAL: Redpanda AND DLQ Failed! Data risk for event {e.event_id}: {dlq_err}")

    # DB write with retry
    async def persist_events(session: Any) -> None:
        await persist_bronze_events(session, events)

    try:
        await db_write(persist_events)
        logger.info(f"Saved {len(events)} events to Bronze DB.")
    except Exception as e:
        logger.error(f"DB Write Error: {e}")
        traceback.print_exc()


@db_retry
async def save_signals_to_db(signals: List[SilverSignal]) -> None:
    if not signals:
        return

    async def persist_signals(session: Any) -> None:
        await persist_silver_signals(session, signals)

    try:
        await db_write(persist_signals)
        logger.info(f"Saved {len(signals)} signals (features) to DB.")
    except Exception as e:
        logger.error(f"DB Write Error (Silver): {e}")


@db_retry
async def save_silver_data(events: List[BronzeEvent]) -> None:
    """
    Persists normalized events to their respective Silver SQL tables.
    PRD 6.2 requirement.
    """
    if not events:
        return

    async def persist_silver(session: Any) -> None:
        await persist_silver_from_bronze(session, events)

    try:
        await db_write(persist_silver)
        logger.info(
            "Saved Silver Data",
            extra={"event_type": "SILVER_WRITE_OK", "bronze_events": len(events)},
        )
    except Exception as e:
        logger.error(f"DB Write Error (Silver Data): {e}")


@db_retry
async def save_candidates_to_db(candidates: List[CandidateTrade]) -> None:
    if not candidates:
        return

    async def persist_cands(session: Any) -> None:
        await persist_candidates(session, candidates)

    try:
        await db_write(persist_cands)
        logger.info(f"Saved {len(candidates)} candidates (GOLD) to DB.")
    except Exception as e:
        logger.error(f"DB Write Error (Gold): {e}")
        return

    # Push to queue for execution service
    try:
        from orion.shared.candidate_queue import CandidateQueue

        queue = await CandidateQueue.get_instance()
        for c in candidates:
            await queue.push(c.candidate_id)
    except Exception as e:
        logger.error(f"Failed to push candidates to queue: {e}")


async def main() -> None:
    global EOD_TRIGGER_LAST_RUN
    global QUALITY_CHECK_LOOP_COUNT
    global _metrics

    # Graceful Shutdown
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received. Stopping ingestion loop...")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info("Starting Orion Ingestion Service...")

    # Initialize metrics once the event loop is running
    if _metrics is None and "init_metrics" in globals():
        try:
            _metrics = await init_metrics()  # type: ignore[arg-type]
        except Exception as metric_err:
            logger.warning(f"Metrics initialization failed: {metric_err}")

    # Optionally reset circuit breaker on start (useful after dev crashes / stale lag)
    if os.getenv("ORION_RESET_CIRCUIT_BREAKER_ON_START", "false").lower() == "true":
        try:
            from orion.core.circuit_breaker import CircuitBreaker

            await CircuitBreaker().close()
        except Exception as cb_err:
            logger.warning(f"Failed to reset circuit breaker on start: {cb_err}")

    # Initialize Redpanda
    producer = await RedpandaProducer.get_instance()
    await producer.start()

    # Initialize Health Monitor
    from orion.core.health_monitor import CriticalHealthException, HealthMonitor

    health_monitor = HealthMonitor()

    # Initialize DB (create tables if not exist)
    await init_db()

    # Initialize Connectors
    uw_base_url = os.getenv("UW_BASE_URL", "https://api.unusualwhales.com/api")
    uw_flow = UWFlowConnector(api_key=system_settings.uw_api_key)
    uw_dark = UWDarkPoolConnector(api_key=system_settings.uw_api_key, base_url=uw_base_url)
    uw_alerts = UWAlertsConnector(api_key=system_settings.uw_api_key, base_url=uw_base_url)

    universe = UniverseManager()
    await universe.hydrate_from_db()

    alpaca = AlpacaMarketConnector(
        api_key=system_settings.alpaca_api_key,
        secret_key=system_settings.alpaca_secret_key,
        paper=system_settings.alpaca_paper,
    )

    feature_engine = FeatureEngine()
    rule_engine = RuleEngine()
    lakehouse = LakehouseWriter()

    # Initialize Calendar
    xcals.get_calendar("XNYS")

    # Timezone for Market Hours logic (ET)
    import pytz

    eastern = pytz.timezone("America/New_York")

    logger.info("Connectors initialized. Starting polling loop.")

    # Adaptive polling intervals (API optimization)
    # Core hours (9:30 AM - 4:00 PM ET): 5 min polling for real-time trading
    # Extended hours (4:00 AM - 9:30 AM, 4:00 PM - 8:00 PM ET): 15 min polling
    CORE_HOURS_INTERVAL = 300.0  # 5 minutes
    EXTENDED_HOURS_INTERVAL = 900.0  # 15 minutes

    def get_polling_interval(now_et: datetime) -> float:
        """Return appropriate polling interval based on market hours."""
        hour = now_et.hour
        minute = now_et.minute
        # Core hours: 9:30 AM - 4:00 PM ET
        if (hour == 9 and minute >= 30) or (10 <= hour < 16):
            return CORE_HOURS_INTERVAL
        # Extended hours: 4:00 AM - 9:30 AM, 4:00 PM - 8:00 PM ET
        return EXTENDED_HOURS_INTERVAL

    while not shutdown_event.is_set():
        try:
            start_time = asyncio.get_running_loop().time()
            all_events = []
            trace_id = str(uuid.uuid4())

            # --- Overnight Sleep Logic ---
            # Active Window: Mon-Fri, 04:00 ET to 20:00 ET.
            now_utc = datetime.now(timezone.utc)
            now_et = now_utc.astimezone(eastern)

            # Determine adaptive polling interval
            loop_interval = get_polling_interval(now_et)

            # Check if we are in active hours
            is_weekday = now_et.weekday() < 5  # 0=Mon, 4=Fri
            is_active_time = 4 <= now_et.hour < 20

            if not (is_weekday and is_active_time):
                # Calculate sleep duration until next 04:00 ET
                # If it's a weekday but after 20:00, next wake is tomorrow 04:00.
                # If it's Friday after 20:00 or Sat/Sun, next wake is Monday 04:00.

                # Start with next day 4am
                next_wake = now_et.replace(hour=4, minute=0, second=0, microsecond=0) + timedelta(days=1)

                # If we are currently before 4am on a weekday, safe to just wait until today 4am?
                # Actually, if now_et.hour < 4 and it is a weekday, we just need to wait until today 4am.
                if is_weekday and now_et.hour < 4:
                    next_wake = now_et.replace(hour=4, minute=0, second=0, microsecond=0)

                # Adjust for weekend
                # If next_wake is Sat (5) -> add 2 days -> Mon (0)
                # If next_wake is Sun (6) -> add 1 day -> Mon (0)
                while next_wake.weekday() >= 5:
                    next_wake += timedelta(days=1)

                sleep_seconds = (next_wake - now_et).total_seconds()

                # Safety clamp: Ensure we don't sleep negative or crazy amounts,
                # though logic above should cover it.
                if sleep_seconds > 0:
                    logger.info(
                        f"Outside active hours (04:00-20:00 ET). Sleeping until {next_wake} ET ({sleep_seconds / 3600:.1f} hours).",
                        extra={"event_type": "SLEEP_OVERNIGHT", "next_wake_et": next_wake.isoformat()},
                    )

                    # We await in chunks to allow for shutdown signals
                    chunk = 60.0  # Check shutdown every minute
                    while sleep_seconds > 0 and not shutdown_event.is_set():
                        wait = min(chunk, sleep_seconds)
                        await asyncio.sleep(wait)
                        sleep_seconds -= wait
                        health_monitor.update_heartbeat()  # Keep heartbeat alive so we don't look dead

                    if shutdown_event.is_set():
                        break

                    # Refresh start_time after waking up so we don't calc weird elapsed times
                    # Continue to start of loop
                    continue

            # Update Heartbeat
            health_monitor.update_heartbeat()

            # 1. Poll UW
            try:
                # lookback_seconds only applies on cold start (no watermark); after first poll, watermarks take over
                flow_events = await uw_flow.poll(lookback_seconds=300)
                dark_events = await uw_dark.fetch_events(lookback_seconds=300)
                alert_events = await uw_alerts.fetch_events(lookback_seconds=300)

                # Check Lag for UW based on freshest event only (avoid tripping breaker due to old records in a batch)
                uw_events = flow_events + dark_events + alert_events
                newest = max((e.event_ts_utc for e in uw_events if e.event_ts_utc), default=None)
                if newest:
                    try:
                        await health_monitor.check_lag(newest)
                    except CriticalHealthException as che:
                        logger.critical(f"HEALTH MONITOR TRIGGERED: {che}")
                        # Keep running; breaker state is handled via DB and observed by other services.
                        pass

                # 2. Update Universe
                for evt in flow_events + dark_events + alert_events:
                    if not getattr(evt, "ingest", None):
                        evt.ingest = {
                            "connector": (
                                "uw_flow"
                                if evt.event_type == "UW_FLOW"
                                else "uw_darkpool"
                                if evt.event_type == "UW_DARKPOOL"
                                else "uw_alerts"
                            ),
                            "run_id": RUN_ID,
                            "trace_id": trace_id,
                            "attempt": 1,
                        }
                    universe.update_from_event(evt)
                    all_events.append(evt)

            except Exception as e:
                logger.error(f"Error polling UW: {e}", extra={"trace_id": trace_id, "event_type": "UW_POLL_ERROR"})

            # ... (Alpaca polling) ...
            # 3. Poll Alpaca for Active Universe
            active_tickers = universe.get_active_universe()
            if active_tickers:
                try:
                    alpaca_events = alpaca.poll(
                        active_tickers, default_lookback_minutes=system_settings.alpaca_lookback_minutes
                    )

                    # Check Lag for Alpaca based on freshest event only (avoid tripping breaker on backfill batches)
                    if alpaca_events:
                        newest = max((e.event_ts_utc for e in alpaca_events if e.event_ts_utc), default=None)
                        if newest:
                            try:
                                await health_monitor.check_lag(newest)
                            except CriticalHealthException as che:
                                logger.critical(f"HEALTH MONITOR TRIGGERED (Alpaca): {che}")

                    all_events.extend(alpaca_events)
                    for evt in alpaca_events:
                        if not getattr(evt, "ingest", None):
                            evt.ingest = {
                                "connector": "alpaca_market",
                                "run_id": RUN_ID,
                                "trace_id": trace_id,
                                "attempt": 1,
                            }
                except Exception as e:
                    logger.error(
                        f"Error polling Alpaca: {e}", extra={"trace_id": trace_id, "event_type": "ALPACA_POLL_ERROR"}
                    )

            # ... (Processing) ...

            # 5. Write to Storage (and Redpanda)
            if all_events:
                async with async_session_factory() as session:
                    deduper = DeduplicationEngine(session)
                    processed_events = []

                    from orion.core.timekeeping import derive_trading_date_and_session

                    for evt in all_events:
                        # Store raw payload in bronze - DO NOT normalize here
                        # Normalization happens in save_silver_data()
                        raw_payload = evt.payload  # Keep raw for bronze

                        # Extract ticker from raw payload if not set
                        if not evt.ticker:
                            evt.ticker = (
                                raw_payload.get("ticker") or raw_payload.get("underlying") or raw_payload.get("symbol")
                            )

                        if evt.event_ts_utc and evt.session is None:
                            evt.event_ts_utc = ensure_utc(evt.event_ts_utc)
                            td, sess = derive_trading_date_and_session(evt.event_ts_utc)
                            evt.trading_date = td
                            evt.session = sess
                        if evt.event_ts_utc and evt.trading_date is None:
                            td, _ = derive_trading_date_and_session(evt.event_ts_utc)
                            evt.trading_date = td
                        if evt.session is None:
                            evt.session = "CLOSED"

                        if getattr(evt, "ingest", None):
                            evt.ingest.setdefault("run_id", RUN_ID)
                            evt.ingest.setdefault("trace_id", trace_id)
                            evt.ingest.setdefault("attempt", 1)
                        else:
                            evt.ingest = {"connector": "unknown", "run_id": RUN_ID, "trace_id": trace_id, "attempt": 1}

                        if evt.received_ts_utc is None:
                            evt.received_ts_utc = datetime.now(timezone.utc)

                        # Keep raw payload for bronze storage
                        evt.payload = raw_payload
                        processed_events.append(evt)

                    unique_events = await deduper.dedupe_batch(processed_events)

                    if unique_events:
                        await save_events_to_db(unique_events)
                        # Persist Normalized Silver Data (normalizer runs here)
                        await save_silver_data(unique_events)
                        all_events = unique_events

                        # Metrics: track events by source
                        if _metrics:
                            for evt in unique_events:
                                _metrics.ingest_events_total.labels(source=evt.source).inc()

                # Feature Engine, etc...
                # Update in-memory UW flow state so OHLCV signals can be enriched with recent flow features.
                try:
                    feature_engine.process_uw_flow(all_events)
                except Exception as e:
                    logger.error(f"Feature Engine (UW Flow State) Error: {e}")

                # Rule-first candidates from UW_FLOW events (PRD 9.1)
                uw_flow_events_only = [e for e in all_events if e.event_type == "UW_FLOW"]
                if uw_flow_events_only:
                    try:
                        uw_signals = feature_engine.process_uw_flow_events(uw_flow_events_only)
                        if uw_signals:
                            await save_signals_to_db(uw_signals)
                            # Persist to Gold layer for model training (PRD 6.3)
                            await feature_engine.persist_signal_batch(uw_signals, "v1_legacy")

                            # === ML SCORING PATH (Pure ML, no rule pre-filter) ===
                            try:
                                from orion.ml.flow_processor import MLFlowProcessor

                                flow_dicts = [e.payload for e in uw_flow_events_only if e.payload]
                                if flow_dicts:
                                    ml_processor = MLFlowProcessor(score_threshold=0.5)
                                    # Use enriched scoring for feature parity with training
                                    ml_candidates = await ml_processor.process_flows_enriched(flow_dicts)
                                    if ml_candidates:
                                        await save_candidates_to_db(ml_candidates)
                                        logger.info(
                                            f"ML Scorer generated {len(ml_candidates)} candidates (enriched)",
                                            extra={"event": "ml_candidates_enriched", "count": len(ml_candidates)},
                                        )
                                        if _metrics:
                                            _metrics.ingest_candidates_total.inc(len(ml_candidates))
                            except Exception as ml_err:
                                logger.warning(f"ML Scoring path error (non-fatal): {ml_err}")

                            # === LEGACY RULE ENGINE PATH ===
                            try:
                                uw_candidates = rule_engine.process_signals(uw_signals)
                                if uw_candidates:
                                    # Note: These may duplicate ML candidates, dedup happens at execution
                                    logger.debug(f"Rule engine generated {len(uw_candidates)} candidates")
                            except Exception as e:
                                logger.error(f"Rule Engine Error (UW): {e}")
                    except Exception as e:
                        logger.error(f"Feature Engine Error (UW): {e}")

                alpaca_events_only = [e for e in all_events if e.event_type == "ALPACA_BAR_1M"]
                if alpaca_events_only:
                    try:
                        bar_signals = feature_engine.process_alpaca_bars(alpaca_events_only)
                        if bar_signals:
                            await save_signals_to_db(bar_signals)
                            # Persist to Gold layer for model training (PRD 6.3)
                            await feature_engine.persist_signal_batch(bar_signals, "v1_legacy")
                            # Rule Engine
                            try:
                                candidates = rule_engine.process_signals(bar_signals)
                                if candidates:
                                    await save_candidates_to_db(candidates)
                                    # Metrics: track candidates
                                    if _metrics:
                                        _metrics.ingest_candidates_total.inc(len(candidates))
                            except Exception as e:
                                logger.error(f"Rule Engine Error: {e}")
                    except Exception as e:
                        logger.error(f"Feature Engine Error: {e}")

                # Lakehouse
                if lakehouse:
                    try:
                        lakehouse.write_events(all_events)
                    except Exception as e:
                        logger.error(
                            f"Lakehouse Write Error: {e}",
                            extra={"event_type": "LAKE_WRITE_FAILED"},
                        )
                        try:
                            from orion.shared.dlq_utils import DLQWriter

                            await DLQWriter.write_to_dlq(
                                error=e,
                                event_type="LAKE_WRITE_FAILED",
                                source="LakehouseWriter",
                                payload={
                                    "count": len(all_events),
                                    "event_ids": [ev.event_id for ev in all_events[:50]],
                                },
                                context="Failed to write lakehouse batch; see logs for details",
                                run_id=RUN_ID,
                                trace_id=trace_id,
                            )
                        except Exception as dlq_err:
                            logger.critical(f"Failed to DLQ lakehouse write failure: {dlq_err}")

                # 6. EOD Review Trigger (PRD 13)
                # Run at 20:05 ET (approx 01:05 UTC, depending on DST).
                # Simple check: If time is between 01:00 and 01:10 UTC AND we haven't run today.
                # NOTE: Ideally this observes market holidays. For now, simple daily check.

                now_utc = datetime.utcnow()
                # 20:05 ET is roughly 00:05 - 01:05 UTC. Let's aim for 01:05 UTC to be safe for both DSTs (post 8pm ET).

                if now_utc.hour == 1 and now_utc.minute >= 5:
                    today_str = now_utc.date().isoformat()
                    if EOD_TRIGGER_LAST_RUN != today_str:
                        logger.info("Triggering EOD Review Agent...")
                        try:
                            # Run in background to not block ingestion
                            asyncio.create_task(run_eod_task())
                            EOD_TRIGGER_LAST_RUN = today_str
                        except Exception as e:
                            logger.error(f"Failed to trigger EOD Agent: {e}")

                # 7. Data Quality Check (runs every ~60 loops / 1 hour)
                QUALITY_CHECK_LOOP_COUNT += 1
                if QUALITY_CHECK_LOOP_COUNT >= 60:
                    QUALITY_CHECK_LOOP_COUNT = 0
                    try:
                        from orion.jobs.data_quality_checker import run_quality_checks

                        asyncio.create_task(run_quality_checks())
                        logger.info("Triggered hourly data quality check")
                    except Exception as e:
                        logger.error(f"Failed to run data quality check: {e}")

        except Exception as e:
            logger.error(f"Main Ingestion Loop Error: {e}")
            # DLQ Logic
            try:
                async with async_session_factory() as session:
                    dlq_entry = DeadLetterQueue(
                        error_message=str(e),
                        stack_trace=traceback.format_exc(),
                        source="INGEST_LOOP",
                        event_type="UNKNOWN",
                        payload={"context": "Main Loop Crash"},
                    )
                    session.add(dlq_entry)
                    await session.commit()
            except Exception as dlq_err:
                logger.critical(f"DLQ Write Failed: {dlq_err}")

            await asyncio.sleep(5.0)

        # Sleep
        elapsed = asyncio.get_running_loop().time() - start_time
        sleep_time = max(0.1, loop_interval - elapsed)

        # Metrics: track loop duration
        if _metrics:
            _metrics.ingest_loop_duration_seconds.observe(elapsed)

        # Log heartbeat
        trace_id = str(uuid.uuid4())
        logger.info(
            "Ingestion heartbeat", extra={"trace_id": trace_id, "context": {"processed_events": len(all_events)}}
        )

        try:
            await health_monitor.check_health()
            await health_monitor.update_db_status(True, "Nominal")
        except CriticalHealthException as che:
            logger.critical(f"HEALTH MONITOR HEARTBEAT FAILURE: {che}")
            await health_monitor.update_db_status(False, str(che))

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=sleep_time)
            break
        except asyncio.TimeoutError:
            pass

    # Stop Redpanda
    await producer.stop()
    logger.info("Ingestion Service Stopped.")


async def run_eod_task() -> None:
    """
    Wrapper to run EOD Agent.
    """
    # Run EOD Review
    from orion.agents.eod_review_agent import EODReviewAgent

    agent = EODReviewAgent()
    await agent.run_review()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
