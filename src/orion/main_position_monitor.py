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
        # Single check mode — trading connectors archived, Data Gateway pending
        logger.warning(
            "Single-check mode skipped: trading connectors archived, awaiting Data Gateway trading proxy integration",
            extra={"event": "monitor_single_check_noop"},
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
