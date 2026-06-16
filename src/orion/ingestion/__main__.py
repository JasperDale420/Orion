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

from orion.ingestion.service import IngestionService
from orion.shared.async_main import run_entrypoint


async def _main() -> None:
    # Construct INSIDE the wrapped coroutine so constructor failures (e.g.
    # Gateway stream client config errors) hit run_entrypoint's structured
    # crash logging instead of escaping before the try.
    service = IngestionService()
    await service.run()


if __name__ == "__main__":
    # run_entrypoint owns asyncio.run, silent Ctrl-C exit, and structured
    # crash logging with a non-zero exit code so docker restart_policy
    # correctly reports failure (was previously ec=0 in restart-loop
    # incidents when the loop returned silently).
    run_entrypoint("orion.ingest", _main())
