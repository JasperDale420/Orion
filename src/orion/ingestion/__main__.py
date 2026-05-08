"""Ingestion Service Entry Point.

Usage:
    python -m orion.ingestion

This runs the modern IngestionService which:
- Polls Alpaca for market data (bars, streams)
- Produces Alpaca bar events for downstream processing
- Relies on external Gateway/Heber pipelines for UW flow/darkpool ingestion
- Processes features and rules
- Writes to Bronze/Silver layers
"""

import asyncio

from orion.ingestion.service import IngestionService
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.ingest")


def main() -> None:
    """Start the ingestion service."""
    service = IngestionService()
    asyncio.run(service.run())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # Ctrl-C path — silent exit is fine, operator initiated.
        pass
    except BaseException:
        # Anything else escaping main() is a silent crash. Without this
        # logger.critical the process exits with a traceback printed to
        # stderr but no structured event, which is invisible to log
        # aggregation. Re-raise so the exit code remains non-zero so
        # docker restart_policy correctly reports failure (was previously
        # ec=0 in restart-loop incidents because main() returned silently
        # rather than propagating the error).
        logger.critical(
            "ingestion_process_crashed",
            extra={"event_type": "INGESTION_PROCESS_CRASHED"},
            exc_info=True,
        )
        raise
