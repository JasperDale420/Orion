"""
Position Monitor CLI.

Runs continuous position monitoring with ML-based exit signals.
Checks open positions and executes exits when triggered.
"""

import argparse
import asyncio
import sys

from orion.execution.position_monitor import run_position_monitor_loop
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.main_position_monitor")


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
        f"Position Monitor starting (interval={args.interval}s, dry_run={args.dry_run})",
        extra={"event": "monitor_cli_start"},
    )

    if args.once:
        # Single check mode
        from orion.config import system_settings
        from orion.connectors.alpaca_trading_connector import AlpacaTradingConnector
        from orion.execution.position_monitor import PositionMonitor

        connector = AlpacaTradingConnector(settings=system_settings)
        monitor = PositionMonitor()

        result = await monitor.run_check(connector, dry_run=args.dry_run)

        logger.info(
            "position_check_result",
            positions_checked=result["positions_checked"],
            exit_signals=result["exit_signals"],
            exits_executed=result["exits_executed"],
        )

        if result.get("exits"):
            for exit_info in result["exits"]:
                logger.info(
                    "exit_detail",
                    symbol=exit_info["symbol"],
                    pnl_pct=f"{exit_info['pnl_pct']:.1f}%",
                    reasoning=exit_info["reasoning"],
                )
    else:
        # Continuous monitoring mode
        await run_position_monitor_loop(
            check_interval_seconds=args.interval,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Position Monitor stopped.")
        sys.exit(0)
