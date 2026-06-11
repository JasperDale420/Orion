"""
Weekly Meta-Search CLI.

Runs the comprehensive weekly evolution cycle:
- Aggregates EOD reports from the week
- Analyzes trade execution quality
- Checks ML model drift
- Generates solver mutations

Scheduled to run Friday EOD after the daily EOD agent.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import aiofiles

from orion.agents.meta_search_agent import MetaSearchAgent
from orion.jobs.solver_promoter import run_solver_promotions
from orion.shared.alerts import send_discord_alert
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.meta_weekly")


async def _save_summary(output_path: str, summary: dict[str, object]) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(path, "w") as f:
        await f.write(json.dumps(summary, indent=2, default=str))


async def main() -> None:
    parser = argparse.ArgumentParser(description="Orion Weekly Meta-Search Agent")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze without creating new solvers",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Path to save the summary JSON",
    )
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="Run in scheduled mode (loop until Friday 5:30 PM EST)",
    )

    args = parser.parse_args()

    logger.info("Starting Orion Weekly Meta-Search Agent...")

    if args.scheduled:
        await run_scheduled()
    else:
        await run_once(args.dry_run, args.output)


async def run_once(dry_run: bool, output_path: str | None) -> None:
    """
    Run the weekly evolution cycle once.
    """
    agent = MetaSearchAgent()

    try:
        summary = await agent.run_weekly_evolution(dry_run=dry_run)

        logger.info(
            f"Weekly evolution completed. "
            f"Reports: {summary['eod_summary']['reports_analyzed']}, "
            f"Mutations: {len(summary.get('mutations_applied', []))}"
        )

        # Process pending solver promotions after evolution
        try:
            promo_result = await run_solver_promotions()
            logger.info(
                "Solver promotion sweep completed",
                promoted=promo_result["promoted"],
                rejected=promo_result["rejected"],
                manual_required=promo_result["manual_required"],
            )
            summary["promotion_result"] = promo_result
        except Exception as e:
            logger.error(f"Solver promotion sweep failed: {e}", exc_info=True)

        # Print summary
        print("\n=== Weekly Evolution Summary ===")
        print(f"Period: {summary['period']['start'][:10]} to {summary['period']['end'][:10]}")
        print(f"EOD Reports Analyzed: {summary['eod_summary']['reports_analyzed']}")
        print(f"Trading Days: {summary['eod_summary']['trading_days']}")
        print(f"Total Decisions: {summary['eod_summary']['total_decisions']}")
        print(f"Executed: {summary['eod_summary']['executed']}")
        print(f"\nExecution Quality: {summary['execution_quality']['execution_health']}")
        print(f"ML Model Health: {summary['ml_drift']['overall_health']}")
        print(f"\nMutations Created: {len(summary.get('mutations_applied', []))}")

        if summary.get("recommendations", {}).get("alerts"):
            print("\nAlerts:")
            for alert in summary["recommendations"]["alerts"]:
                print(f"  [{alert['severity']}] {alert['message']}")

        # Save to file if requested
        if output_path:
            await _save_summary(output_path, summary)
            logger.info(f"Summary saved to {output_path}")

    except Exception as e:
        logger.error(f"Weekly evolution failed: {e}", exc_info=True)
        sys.exit(1)


async def run_scheduled() -> None:
    """
    Run in scheduled mode - execute every Friday at 5:30 PM EST.
    """
    from zoneinfo import ZoneInfo

    est = ZoneInfo("America/New_York")

    logger.info("Running in scheduled mode. Waiting for Friday 5:30 PM ET...")

    while True:
        now = datetime.now(est)

        # Check if it's Friday (weekday 4) at 5:30 PM
        is_friday = now.weekday() == 4
        is_target_time = now.hour == 17 and now.minute == 30

        if is_friday and is_target_time:
            logger.info("Friday 5:30 PM EST - Starting weekly evolution")

            agent = MetaSearchAgent()
            try:
                summary = await agent.run_weekly_evolution(dry_run=False)

                # Save summary
                output_path = f"artifacts/reports/weekly_evolution_{now.strftime('%Y-%m-%d')}.json"
                await _save_summary(output_path, summary)

                logger.info(f"Weekly evolution completed. Summary saved to {output_path}")

                reports = summary.get("eod_summary", {}).get("reports_analyzed", 0)
                mutations = len(summary.get("mutations_applied", []))

                # Process pending solver promotions after evolution
                promoted = 0
                try:
                    promo_result = await run_solver_promotions()
                    promoted = promo_result["promoted"]
                    logger.info(
                        "Solver promotion sweep completed",
                        promoted=promo_result["promoted"],
                        rejected=promo_result["rejected"],
                        manual_required=promo_result["manual_required"],
                    )
                except Exception as promo_err:
                    logger.error(f"Solver promotion sweep failed: {promo_err}", exc_info=True)

                await send_discord_alert(
                    f"Meta-weekly evolution completed ({now.strftime('%Y-%m-%d')}): "
                    f"{reports} EOD reports analyzed, {mutations} mutations applied, "
                    f"{promoted} solvers promoted."
                )

            except Exception as e:
                logger.error(f"Weekly evolution failed: {e}", exc_info=True)
                await send_discord_alert(
                    f"Meta-weekly evolution FAILED ({now.strftime('%Y-%m-%d')}): {type(e).__name__}: {e}"
                )

            # Wait 1 hour to avoid re-triggering
            await asyncio.sleep(3600)
        else:
            # Check every minute
            await asyncio.sleep(60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Weekly Meta-Search Agent stopped.")
