"""
Position Monitor CLI.

Runs continuous position monitoring with ML-based exit signals.
Checks open positions and executes exits when triggered.
"""

import argparse

from orion.clients.gateway_trading_client import get_gateway_trading_client
from orion.config import system_settings
from orion.execution.position_monitor import (
    GatewayPositionAdapter,
    get_position_monitor,
    run_position_monitor_loop,
)
from orion.shared.async_main import run_entrypoint
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.main_position_monitor")


async def _build_execution_engine():
    """Initialize ExecutionEngine with risk state synced from Gateway."""
    from orion.execution.execution_engine import ExecutionEngine

    engine = ExecutionEngine()
    await engine.initialize()
    return engine


async def main() -> None:
    parser = argparse.ArgumentParser(description="Orion Position Monitor")
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Seconds between position checks (default: 60)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log exit signals but don't execute",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one check and exit (for testing)",
    )

    args = parser.parse_args()

    logger.info(
        f"Position Monitor starting (interval={args.interval}s, dry_run={args.dry_run}, stage={system_settings.orion_stage})",
        extra={"event": "monitor_cli_start", "stage": system_settings.orion_stage},
    )

    # Initialize Gateway trading client and ExecutionEngine
    gateway_client = get_gateway_trading_client()
    execution_engine = await _build_execution_engine()

    if args.once:
        # Single check mode via GatewayTradingClient
        adapter = GatewayPositionAdapter(gateway_client)
        await adapter.refresh()
        monitor = get_position_monitor(execution_engine=execution_engine)
        summary = await monitor.run_check(adapter, dry_run=args.dry_run)
        logger.info(
            "Single check complete",
            extra={"event": "monitor_single_check_done", **summary},
        )
    else:
        # Continuous monitoring mode
        await run_position_monitor_loop(
            check_interval_seconds=args.interval,
            dry_run=args.dry_run,
            execution_engine=execution_engine,
            gateway_client=gateway_client,
        )


if __name__ == "__main__":
    run_entrypoint("orion.main_position_monitor", main())
