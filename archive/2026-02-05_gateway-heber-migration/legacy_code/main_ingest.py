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
from orion.connectors.alpaca_stream_connector import AlpacaStreamConnector
from orion.connectors.uw_alerts_connector import UWAlertsConnector
from orion.connectors.uw_darkpool_connector import UWDarkPoolConnector
from orion.connectors.uw_flow_connector import UWFlowConnector
from orion.core.health_monitor import CriticalHealthException, HealthMonitor
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

    try:
        from orion.shared.candidate_queue import CandidateQueue

        queue = await CandidateQueue.get_instance()
        for c in candidates:
            await queue.push(c.candidate_id)
    except Exception as e:
        logger.error(f"Failed to push candidates to queue: {e}")


# --- Helper functions for main() to reduce cognitive complexity ---


async def _initialize_resources() -> (
    tuple[
        "RedpandaProducer",
        "HealthMonitor",
        "UWFlowConnector",
        "UWDarkPoolConnector",
        "UWAlertsConnector",
        "UniverseManager",
        "AlpacaMarketConnector",
        "AlpacaStreamConnector | None",
        "FeatureEngine",
        "RuleEngine",
        "LakehouseWriter",
        "Metrics | None",
    ]
):
    """Initialize all resources and connectors."""
    from orion.core.health_monitor import HealthMonitor

    global _metrics

    logger.info("Starting Orion Ingestion Service...")

    # Initialize metrics
    if _metrics is None and "init_metrics" in globals():
        try:
            _metrics = await init_metrics()  # type: ignore[arg-type]
        except Exception as metric_err:
            logger.warning(f"Metrics initialization failed: {metric_err}")

    # Reset circuit breaker if configured
    if os.getenv("ORION_RESET_CIRCUIT_BREAKER_ON_START", "false").lower() == "true":
        try:
            from orion.core.circuit_breaker import CircuitBreaker

            await CircuitBreaker().close()
        except Exception as cb_err:
            logger.warning(f"Failed to reset circuit breaker on start: {cb_err}")

    # Initialize Redpanda and Health Monitor
    producer = await RedpandaProducer.get_instance()
    await producer.start()
    health_monitor = HealthMonitor()

    # Initialize DB
    await init_db()

    # Initialize Connectors
    gateway_url = os.getenv("GATEWAY_URL", "http://localhost:8080")
    uw_flow = UWFlowConnector(gateway_url=gateway_url)
    uw_dark = UWDarkPoolConnector(gateway_url=gateway_url)
    uw_alerts = UWAlertsConnector(gateway_url=gateway_url)

    universe = UniverseManager()
    await universe.hydrate_from_db()

    alpaca = AlpacaMarketConnector(
        api_key=system_settings.alpaca_api_key,
        secret_key=system_settings.alpaca_secret_key,
        paper=system_settings.alpaca_paper,
    )

    # Initialize streaming connector
    alpaca_stream: AlpacaStreamConnector | None = None
    use_streaming = os.getenv("ORION_USE_ALPACA_STREAMING", "true").lower() == "true"
    if use_streaming:
        try:
            alpaca_stream = AlpacaStreamConnector(
                api_key=system_settings.alpaca_api_key,
                secret_key=system_settings.alpaca_secret_key,
                feed="sip",
            )
        except Exception as e:
            logger.warning(f"Failed to create streaming connector, using polling: {e}")

    feature_engine = FeatureEngine()
    rule_engine = RuleEngine()
    lakehouse = LakehouseWriter()

    # Initialize Calendar
    xcals.get_calendar("XNYS")

    logger.info("Connectors initialized. Starting polling loop.")

    return (
        producer,
        health_monitor,
        uw_flow,
        uw_dark,
        uw_alerts,
        universe,
        alpaca,
        alpaca_stream,
        feature_engine,
        rule_engine,
        lakehouse,
        _metrics,
    )


async def _start_background_jobs(alpaca_stream: "AlpacaStreamConnector | None", universe: UniverseManager) -> None:
    """Start background jobs like rollup and window features."""
    # Start rollup job
    try:
        from orion.jobs.rollup_job import RollupJob

        rollup_job = RollupJob(loop_interval_seconds=60.0)
        _rollup_task = asyncio.create_task(rollup_job.run_forever())  # noqa: F841
        logger.info("Rollup job started as background task")
    except Exception as e:
        logger.warning(f"Failed to start rollup job: {e}")

    # Start window feature job
    try:
        from orion.jobs.window_feature_job import WindowFeatureJob

        window_job = WindowFeatureJob(loop_interval_seconds=300.0)
        _window_task = asyncio.create_task(window_job.run_forever())  # noqa: F841
        logger.info("Window feature job started as background task")
    except Exception as e:
        logger.warning(f"Failed to start window feature job: {e}")

    # Start Alpaca streaming
    if alpaca_stream:
        try:
            active_tickers = universe.get_active_universe()
            if active_tickers:
                await alpaca_stream.subscribe(active_tickers)
            await alpaca_stream.start()
            logger.info(f"Alpaca WebSocket streaming started for {len(active_tickers or [])} tickers")
        except Exception as e:
            logger.warning(f"Failed to start Alpaca streaming, using polling: {e}")


def _get_polling_interval(now_et: datetime) -> float:
    """Return appropriate polling interval based on market hours."""
    CORE_HOURS_INTERVAL = 300.0  # 5 minutes
    EXTENDED_HOURS_INTERVAL = 900.0  # 15 minutes

    hour = now_et.hour
    minute = now_et.minute
    # Core hours: 9:30 AM - 4:00 PM ET
    if (hour == 9 and minute >= 30) or (10 <= hour < 16):
        return CORE_HOURS_INTERVAL
    return EXTENDED_HOURS_INTERVAL


async def _check_overnight_sleep(
    now_et: datetime,
    shutdown_event: asyncio.Event,
    health_monitor: "HealthMonitor",
) -> bool:
    """Check if we should sleep during off-hours. Returns True if should continue main loop."""
    is_weekday = now_et.weekday() < 5
    is_active_time = 4 <= now_et.hour < 20

    if is_weekday and is_active_time:
        return False  # No sleep needed

    # Calculate sleep duration until next 04:00 ET
    next_wake = now_et.replace(hour=4, minute=0, second=0, microsecond=0) + timedelta(days=1)

    # Handle early morning weekday case
    if is_weekday and now_et.hour < 4:
        next_wake = now_et.replace(hour=4, minute=0, second=0, microsecond=0)

    # Adjust for weekend
    while next_wake.weekday() >= 5:
        next_wake += timedelta(days=1)

    sleep_seconds = (next_wake - now_et).total_seconds()

    if sleep_seconds > 0:
        logger.info(
            f"Outside active hours (04:00-20:00 ET). Sleeping until {next_wake} ET ({sleep_seconds / 3600:.1f} hours).",
            extra={"event_type": "SLEEP_OVERNIGHT", "next_wake_et": next_wake.isoformat()},
        )

        chunk = 60.0
        while sleep_seconds > 0 and not shutdown_event.is_set():
            wait = min(chunk, sleep_seconds)
            await asyncio.sleep(wait)
            sleep_seconds -= wait
            health_monitor.update_heartbeat()

    return True  # Should continue to next iteration


async def _poll_uw_connectors(
    uw_flow: UWFlowConnector,
    uw_dark: UWDarkPoolConnector,
    uw_alerts: UWAlertsConnector,
    health_monitor: "HealthMonitor",
    universe: UniverseManager,
    trace_id: str,
) -> List[BronzeEvent]:
    """Poll all UW connectors and return combined events."""
    from orion.core.health_monitor import CriticalHealthException

    events: List[BronzeEvent] = []

    try:
        flow_events = await uw_flow.poll(lookback_seconds=300)
        dark_events = await uw_dark.fetch_events(lookback_seconds=300)
        alert_events = await uw_alerts.fetch_events(lookback_seconds=300)

        uw_events = flow_events + dark_events + alert_events

        # Check lag
        newest = max((e.event_ts_utc for e in uw_events if e.event_ts_utc), default=None)
        if newest:
            try:
                await health_monitor.check_lag(newest)
            except CriticalHealthException as che:
                logger.critical(f"HEALTH MONITOR TRIGGERED: {che}")

        # Tag metadata and update universe
        for evt in uw_events:
            if not getattr(evt, "ingest", None):
                connector_name = "uw_flow"
                if evt.event_type == "UW_DARKPOOL":
                    connector_name = "uw_darkpool"
                elif evt.event_type == "UW_ALERT":
                    connector_name = "uw_alerts"
                evt.ingest = {
                    "connector": connector_name,
                    "run_id": RUN_ID,
                    "trace_id": trace_id,
                    "attempt": 1,
                }
            universe.update_from_event(evt)
            events.append(evt)

    except Exception as e:
        logger.error(f"Error polling UW: {e}", extra={"trace_id": trace_id, "event_type": "UW_POLL_ERROR"})

    return events


async def _poll_alpaca(
    alpaca: AlpacaMarketConnector,
    alpaca_stream: "AlpacaStreamConnector | None",
    universe: UniverseManager,
    health_monitor: "HealthMonitor",
    trace_id: str,
) -> List[BronzeEvent]:
    """Poll Alpaca for market data events."""
    from orion.core.health_monitor import CriticalHealthException

    events: List[BronzeEvent] = []
    active_tickers = universe.get_active_universe()

    if not active_tickers:
        return events

    try:
        # Use streaming if available
        if alpaca_stream and alpaca_stream.is_running:
            new_tickers = set(active_tickers) - alpaca_stream.subscribed_tickers
            if new_tickers:
                await alpaca_stream.subscribe(list(new_tickers))
            alpaca_events = await alpaca_stream.drain_events()
            connector_name = "alpaca_stream"
        else:
            alpaca_events = alpaca.poll(
                active_tickers, default_lookback_minutes=system_settings.alpaca_lookback_minutes
            )
            connector_name = "alpaca_market"

        # Check lag
        if alpaca_events:
            newest = max((e.event_ts_utc for e in alpaca_events if e.event_ts_utc), default=None)
            if newest:
                try:
                    await health_monitor.check_lag(newest)
                except CriticalHealthException as che:
                    logger.critical(f"HEALTH MONITOR TRIGGERED (Alpaca): {che}")

        for evt in alpaca_events:
            if not getattr(evt, "ingest", None):
                evt.ingest = {
                    "connector": connector_name,
                    "run_id": RUN_ID,
                    "trace_id": trace_id,
                    "attempt": 1,
                }
        events.extend(alpaca_events)

    except Exception as e:
        logger.error(f"Error getting Alpaca bars: {e}", extra={"trace_id": trace_id, "event_type": "ALPACA_ERROR"})

    return events


async def _process_and_persist_events(
    all_events: List[BronzeEvent], trace_id: str, metrics: "Metrics | None"
) -> List[BronzeEvent]:
    """Deduplicate, enrich, and persist events to storage."""
    from orion.core.timekeeping import derive_trading_date_and_session

    async with async_session_factory() as session:
        deduper = DeduplicationEngine(session)
        processed_events = []

        for evt in all_events:
            raw_payload = evt.payload

            if not evt.ticker:
                evt.ticker = raw_payload.get("ticker") or raw_payload.get("underlying") or raw_payload.get("symbol")

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

            evt.payload = raw_payload
            processed_events.append(evt)

        unique_events = await deduper.dedupe_batch(processed_events)

        if unique_events:
            await save_events_to_db(unique_events)
            await save_silver_data(unique_events)

            if metrics:
                for evt in unique_events:
                    metrics.ingest_events_total.labels(source=evt.source).inc()

        return unique_events


async def _run_feature_and_rule_pipelines(
    all_events: List[BronzeEvent],
    feature_engine: FeatureEngine,
    rule_engine: RuleEngine,
    metrics: "Metrics | None",
) -> None:
    """Run feature extraction and rule engine for events."""
    try:
        feature_engine.process_uw_flow(all_events)
    except Exception as e:
        logger.error(f"Feature Engine (UW Flow State) Error: {e}")

    # Process UW Flow events
    uw_flow_events = [e for e in all_events if e.event_type == "UW_FLOW"]
    if uw_flow_events:
        await _process_uw_flow_pipeline(uw_flow_events, feature_engine, rule_engine, metrics)

    # Process Alpaca events
    alpaca_events = [e for e in all_events if e.event_type == "ALPACA_BAR_1M"]
    if alpaca_events:
        await _process_alpaca_pipeline(alpaca_events, feature_engine, rule_engine, metrics)


async def _process_uw_flow_pipeline(
    events: List[BronzeEvent],
    feature_engine: FeatureEngine,
    rule_engine: RuleEngine,
    metrics: "Metrics | None",
) -> None:
    """Process UW flow events through feature and rule pipeline."""
    try:
        uw_signals = feature_engine.process_uw_flow_events(events)
        if uw_signals:
            await save_signals_to_db(uw_signals)
            await feature_engine.persist_signal_batch(uw_signals, "v1_legacy")

            # ML Scoring Path
            try:
                from orion.ml.flow_processor import MLFlowProcessor

                flow_dicts = []
                for e in events:
                    if e.payload:
                        flow_dict = dict(e.payload)
                        flow_dict["event_id"] = e.event_id
                        flow_dicts.append(flow_dict)

                if flow_dicts:
                    ml_processor = MLFlowProcessor(score_threshold=0.5)
                    ml_candidates = await ml_processor.process_flows_enriched(flow_dicts)
                    if ml_candidates:
                        await save_candidates_to_db(ml_candidates)
                        logger.info(
                            f"ML Scorer generated {len(ml_candidates)} candidates (enriched)",
                            extra={"event": "ml_candidates_enriched", "count": len(ml_candidates)},
                        )
                        if metrics:
                            metrics.ingest_candidates_total.inc(len(ml_candidates))
            except Exception as ml_err:
                logger.warning(f"ML Scoring path error (non-fatal): {ml_err}")

            # Legacy Rule Engine
            try:
                uw_candidates = rule_engine.process_signals(uw_signals)
                if uw_candidates:
                    logger.debug(f"Rule engine generated {len(uw_candidates)} candidates")
            except Exception as e:
                logger.error(f"Rule Engine Error (UW): {e}")
    except Exception as e:
        logger.error(f"Feature Engine Error (UW): {e}")


async def _process_alpaca_pipeline(
    events: List[BronzeEvent],
    feature_engine: FeatureEngine,
    rule_engine: RuleEngine,
    metrics: "Metrics | None",
) -> None:
    """Process Alpaca events through feature and rule pipeline."""
    try:
        bar_signals = feature_engine.process_alpaca_bars(events)
        if bar_signals:
            await save_signals_to_db(bar_signals)
            await feature_engine.persist_signal_batch(bar_signals, "v1_legacy")

            try:
                candidates = rule_engine.process_signals(bar_signals)
                if candidates:
                    await save_candidates_to_db(candidates)
                    if metrics:
                        metrics.ingest_candidates_total.inc(len(candidates))
            except Exception as e:
                logger.error(f"Rule Engine Error: {e}")
    except Exception as e:
        logger.error(f"Feature Engine Error: {e}")


async def _write_lakehouse(all_events: List[BronzeEvent], lakehouse: LakehouseWriter, trace_id: str) -> None:
    """Write events to lakehouse."""
    try:
        lakehouse.write_events(all_events)
    except Exception as e:
        logger.error(f"Lakehouse Write Error: {e}", extra={"event_type": "LAKE_WRITE_FAILED"})
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


def _check_eod_trigger() -> None:
    """Check if EOD agent should be triggered."""
    global EOD_TRIGGER_LAST_RUN

    now_utc = datetime.now(timezone.utc)
    if now_utc.hour == 1 and now_utc.minute >= 5:
        today_str = now_utc.date().isoformat()
        if EOD_TRIGGER_LAST_RUN != today_str:
            logger.info("Triggering EOD Review Agent...")
            try:
                _eod_task = asyncio.create_task(run_eod_task())  # noqa: F841
                EOD_TRIGGER_LAST_RUN = today_str
            except Exception as e:
                logger.error(f"Failed to trigger EOD Agent: {e}")


def _check_data_quality() -> None:
    """Check if data quality job should run."""
    global QUALITY_CHECK_LOOP_COUNT

    QUALITY_CHECK_LOOP_COUNT += 1
    if QUALITY_CHECK_LOOP_COUNT >= 60:
        QUALITY_CHECK_LOOP_COUNT = 0
        try:
            from orion.jobs.data_quality_checker import run_quality_checks

            _quality_task = asyncio.create_task(run_quality_checks())  # noqa: F841
            logger.info("Triggered hourly data quality check")
        except Exception as e:
            logger.error(f"Failed to run data quality check: {e}")


async def _handle_loop_crash(e: Exception) -> None:
    """Handle crash in main loop by writing to DLQ."""
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


async def _run_health_check(health_monitor: HealthMonitor) -> None:
    """Run health check and update DB status."""
    try:
        await health_monitor.check_health()
        await health_monitor.update_db_status(True, "Nominal")
    except CriticalHealthException as che:
        logger.critical(f"HEALTH MONITOR HEARTBEAT FAILURE: {che}")
        await health_monitor.update_db_status(False, str(che))


async def _wait_for_next_cycle(shutdown_event: asyncio.Event, sleep_time: float) -> bool:
    """Wait for next cycle or shutdown. Returns True if shutdown requested."""
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=sleep_time)
        return True
    except asyncio.TimeoutError:
        return False


async def _run_ingestion_cycle(
    uw_flow: UWFlowConnector,
    uw_dark: UWDarkPoolConnector,
    uw_alerts: UWAlertsConnector,
    universe: UniverseManager,
    alpaca: AlpacaMarketConnector,
    alpaca_stream: "AlpacaStreamConnector | None",
    health_monitor: HealthMonitor,
    feature_engine: FeatureEngine,
    rule_engine: RuleEngine,
    lakehouse: LakehouseWriter,
    metrics: "Metrics | None",
    trace_id: str,
) -> int:
    """Run a single ingestion cycle. Returns count of processed events."""
    # Poll UW connectors
    uw_events = await _poll_uw_connectors(uw_flow, uw_dark, uw_alerts, health_monitor, universe, trace_id)

    # Poll Alpaca
    alpaca_events = await _poll_alpaca(alpaca, alpaca_stream, universe, health_monitor, trace_id)

    # Combine all events
    all_events = uw_events + alpaca_events

    if not all_events:
        return 0

    # Process and persist events
    unique_events = await _process_and_persist_events(all_events, trace_id, metrics)

    if unique_events:
        # Run feature and rule pipelines
        await _run_feature_and_rule_pipelines(unique_events, feature_engine, rule_engine, metrics)

        # Write to lakehouse
        await _write_lakehouse(unique_events, lakehouse, trace_id)

    return len(unique_events)


async def main() -> None:
    """Main ingestion service entry point."""
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

    # Initialize all resources
    (
        producer,
        health_monitor,
        uw_flow,
        uw_dark,
        uw_alerts,
        universe,
        alpaca,
        alpaca_stream,
        feature_engine,
        rule_engine,
        lakehouse,
        _metrics,
    ) = await _initialize_resources()

    # Start background jobs
    await _start_background_jobs(alpaca_stream, universe)

    # Timezone for market hours
    from zoneinfo import ZoneInfo

    eastern = ZoneInfo("America/New_York")

    # Main polling loop
    while not shutdown_event.is_set():
        try:
            start_time = asyncio.get_running_loop().time()
            trace_id = str(uuid.uuid4())

            # Check overnight sleep
            now_utc = datetime.now(timezone.utc)
            now_et = now_utc.astimezone(eastern)
            loop_interval = _get_polling_interval(now_et)

            should_continue = await _check_overnight_sleep(now_et, shutdown_event, health_monitor)
            if should_continue:
                if shutdown_event.is_set():
                    break
                continue

            # Update heartbeat
            health_monitor.update_heartbeat()

            # Run ingestion cycle
            processed_count = await _run_ingestion_cycle(
                uw_flow,
                uw_dark,
                uw_alerts,
                universe,
                alpaca,
                alpaca_stream,
                health_monitor,
                feature_engine,
                rule_engine,
                lakehouse,
                _metrics,
                trace_id,
            )

            # Check EOD trigger
            _check_eod_trigger()

            # Check data quality
            _check_data_quality()

            # Metrics and heartbeat
            elapsed = asyncio.get_running_loop().time() - start_time
            if _metrics:
                _metrics.ingest_loop_duration_seconds.observe(elapsed)

            logger.info(
                "Ingestion heartbeat",
                extra={"trace_id": trace_id, "context": {"processed_events": processed_count}},
            )

            # Health check
            await _run_health_check(health_monitor)

            # Sleep until next cycle
            sleep_time = max(0.1, loop_interval - elapsed)
            if await _wait_for_next_cycle(shutdown_event, sleep_time):
                break

        except Exception as e:
            logger.error(f"Main Ingestion Loop Error: {e}")
            await _handle_loop_crash(e)
            await asyncio.sleep(5.0)

    # Cleanup
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
