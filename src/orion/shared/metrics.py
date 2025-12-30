import logging
import os

from prometheus_client import Counter, Gauge, Histogram, start_http_server

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
    if os.getenv("ORION_METRICS_ENABLED", "false").lower() == "true":
        port = int(os.getenv("ORION_METRICS_PORT", "8000"))
        Metrics.start_server(port)

    return metrics
