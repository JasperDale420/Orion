"""
Automated job to reset the daily dashboard and set the starting equity.
Fetches the current account equity from Alpaca and calls the Orion API endpoints.
Designed to be run as a cron job or scheduled task before market open.

Requires:
  - ALPACA_API_KEY
  - ALPACA_SECRET_KEY
  - ORION_API_KEY
  - ORION_API_URL (defaults to http://localhost:8000)
"""

import asyncio
import logging
import os
import sys

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from orion.config import system_settings
from orion.connectors.alpaca_trading_connector import AlpacaTradingConnector

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

ORION_API_URL = os.getenv("ORION_API_URL", "http://localhost:8000")
ORION_API_KEY = os.getenv("ORION_API_KEY") or system_settings.api_key


@retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(5))
async def call_api(url: str, method: str = "POST", params: dict = None) -> None:
    headers = {}
    if ORION_API_KEY:
        headers["x-api-key"] = ORION_API_KEY
    else:
        logger.warning("No ORION_API_KEY found. Proceeding without authorization header.")

    async with httpx.AsyncClient() as client:
        # We use small timeout but retry
        req = client.build_request(method, url, params=params, headers=headers)
        response = await client.send(req, timeout=10.0)
        response.raise_for_status()
        logger.info(f"Successfully called {url}: {response.json()}")


async def run_once() -> None:
    logger.info("Starting Daily Dashboard Reset Job")

    # 1. Fetch equity from Alpaca
    logger.info("Initializing AlpacaTradingConnector to fetch equity...")
    try:
        alpaca = AlpacaTradingConnector(settings=system_settings)
        account = alpaca.client.get_account()
        equity_str = getattr(account, "equity", "0")
        equity = float(equity_str)
        logger.info(f"Fetched account equity: ${equity:.2f}")
    except Exception as e:
        logger.error(f"Failed to fetch account equity from Alpaca: {e}")
        sys.exit(1)

    # 2. Reset Daily P&L and Trade Counts
    reset_url = f"{ORION_API_URL.rstrip('/')}/dashboard/reset"
    try:
        logger.info("Calling dashboard reset endpoint...")
        await call_api(reset_url)
    except Exception as e:
        logger.error(f"Failed to reset dashboard: {e}")
        sys.exit(1)

    # 3. Set Starting Equity
    equity_url = f"{ORION_API_URL.rstrip('/')}/dashboard/equity"
    try:
        logger.info(f"Setting dashboard equity to ${equity:.2f}...")
        await call_api(equity_url, params={"equity": equity})
    except Exception as e:
        logger.error(f"Failed to set dashboard equity: {e}")
        sys.exit(1)

    logger.info("Daily Dashboard Reset Job completed successfully.")


async def run_scheduled() -> None:
    """Run in scheduled mode - execute every weekday at 9:00 AM EST."""
    import asyncio
    from datetime import datetime
    from zoneinfo import ZoneInfo

    est = ZoneInfo("America/New_York")

    logger.info("Running in scheduled mode. Waiting for weekdays at 9:00 AM ET...")

    while True:
        try:
            now = datetime.now(est)

            # Check if weekday (0-4) and 9:00 AM
            is_weekday = now.weekday() < 5
            is_target_time = now.hour == 9 and now.minute == 0

            if is_weekday and is_target_time:
                logger.info("Weekday 9:00 AM EST - Starting daily dashboard reset")
                # Don't sys.exit on failure in scheduled mode
                try:
                    # Abstracted the logic from run_once into a safe call
                    alpaca = AlpacaTradingConnector(settings=system_settings)
                    account = alpaca.client.get_account()
                    equity = float(getattr(account, "equity", "0"))

                    reset_url = f"{ORION_API_URL.rstrip('/')}/dashboard/reset"
                    await call_api(reset_url)

                    equity_url = f"{ORION_API_URL.rstrip('/')}/dashboard/equity"
                    await call_api(equity_url, params={"equity": equity})

                    logger.info("Scheduled dashboard reset completed successfully.")
                except Exception as e:
                    logger.error(f"Scheduled dashboard reset failed: {e}", exc_info=True)

                # Wait 1 hour to avoid re-triggering
                await asyncio.sleep(3600)
            else:
                # Check every minute
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error in scheduled loop: {e}", exc_info=True)
            await asyncio.sleep(60)


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Orion Daily Dashboard Reset Job")
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="Run in scheduled mode (loop until weekday 9:00 AM EST)",
    )
    args = parser.parse_args()

    if args.scheduled:
        await run_scheduled()
    else:
        await run_once()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Daily Dashboard Reset Job stopped.")
