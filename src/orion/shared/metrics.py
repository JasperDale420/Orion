import logging

from prometheus_client import Counter, Gauge, Histogram, start_http_server

from orion.config import system_settings
from orion.shared.patterns import AsyncSingleton

logger = logging.getLogger(__name__)


class Metrics(AsyncSingleton):
    """
    Singleton wrapper around prometheus_client for system-wide metrics.

    Metrics are exposed via HTTP endpoint for Prometheus scraping.
    """

    def __init__(self) -> None:
        # Ingestion metrics
        self.ingest_events_total = Counter("orion_ingest_events_total", "Total events ingested", ["source"])
        self.ingest_candidates_total = Counter("orion_ingest_candidates_total", "Total candidates created")
        self.ingest_loop_duration_seconds = Histogram(
            "orion_ingest_loop_duration_seconds", "Ingestion loop duration in seconds"
        )

        # Execution metrics
        self.execution_decisions_total = Counter(
            "orion_execution_decisions_total", "Total decisions made", ["decision_type"]
        )
        self.execution_orders_total = Counter("orion_execution_orders_total", "Total orders submitted", ["status"])
        self.execution_latency_seconds = Histogram(
            "orion_execution_latency_seconds", "Execution latency from candidate to order", ["ticker"]
        )
        self.execution_queue_depth = Gauge("orion_execution_queue_depth", "Candidate queue depth")

        # Risk metrics
        self.risk_equity = Gauge("orion_risk_equity", "Current account equity")
        self.risk_exposure = Gauge("orion_risk_exposure", "Position exposure in USD", ["ticker"])
        self.risk_daily_loss = Gauge("orion_risk_daily_loss", "Current daily loss")
        self.risk_open_positions = Gauge("orion_risk_open_positions", "Number of open positions")
        # Fill-quality: adverse slippage between limit and fill, in basis points.
        # RiskManager.process_fill guards emission with hasattr(); this
        # histogram was never defined, so slippage observability was silently
        # dead even after the gauges were fixed.
        self.slippage_bps = Histogram(
            "orion_slippage_bps",
            "Adverse fill slippage vs limit price in basis points",
            ["ticker", "side"],
        )

    async def _async_init(self) -> None:
        """Async initialization hook called once on first instantiation."""
        logger.info("Metrics initialized", extra={"event_type": "METRICS_INIT"})

    @classmethod
    def start_server(cls, port: int = 8000) -> None:
        """Start HTTP server for Prometheus to scrape."""
        try:
            start_http_server(port)
            logger.info(
                f"Metrics HTTP server started on port {port}",
                extra={"event_type": "METRICS_SERVER_START", "port": port},
            )
        except Exception as e:
            logger.error(
                f"Failed to start metrics server: {e}", extra={"event_type": "METRICS_SERVER_ERROR", "error": str(e)}
            )


async def init_metrics() -> Metrics:
    """Initialize and optionally start metrics HTTP server."""
    metrics = await Metrics.get_instance()

    # Start server if enabled
    if system_settings.metrics_enabled:
        Metrics.start_server(system_settings.metrics_port)

    return metrics


def get_metrics_sync() -> Metrics | None:
    """Return the Metrics singleton from synchronous code, creating it if needed.

    ``Metrics.__init__`` is fully synchronous (prometheus collectors register at
    construction; ``_async_init`` only logs), so sync callers like RiskManager
    can safely materialise the singleton without an event loop. The instance is
    registered in the AsyncSingleton registry so a later ``await
    Metrics.get_instance()`` returns the same object. Returns None on any
    failure — metrics must never break the risk path.
    """
    try:
        instance = Metrics._instances.get(Metrics)
        if instance is None:
            instance = Metrics()
            Metrics._instances[Metrics] = instance
        return instance  # type: ignore[return-value]
    except Exception:
        logger.exception("get_metrics_sync failed; metrics disabled for this caller")
        return None
