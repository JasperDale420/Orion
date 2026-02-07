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


def main() -> None:
    """Start the ingestion service."""
    service = IngestionService()
    asyncio.run(service.run())


if __name__ == "__main__":
    main()
