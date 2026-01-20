"""
Historical Feature Backfill Script.

Fetches historical GEX, Market Tide, Max Pain data for past dates
to enable proper feature enrichment during label backfill.
"""

import asyncio
import os
from datetime import date, timedelta
from typing import List

from dotenv import load_dotenv

load_dotenv()

from orion.connectors.uw_greek_exposure_connector import UWGreekExposureConnector
from orion.connectors.uw_market_tide_connector import UWMarketTideConnector
from orion.connectors.uw_max_pain_connector import UWMaxPainConnector
from orion.storage.db import init_db

# Get tickers from flow data
TICKERS = [
    "SPY",
    "QQQ",
    "TSLA",
    "NVDA",
    "AAPL",
    "AMD",
    "META",
    "AMZN",
    "GOOG",
    "MSFT",
    "MSTR",
    "GLD",
    "SLV",
    "IWM",
    "XLF",
    "XLE",
    "BA",
    "JPM",
    "BAC",
    "COIN",
]


async def backfill_market_tide(api_key: str, start_date: date, end_date: date) -> int:
    """Backfill market tide for date range."""
    connector = UWMarketTideConnector(api_key)
    total = 0

    current = start_date
    while current <= end_date:
        print(f"  Market Tide: {current}")
        count = await connector.fetch_and_store(current)
        total += count
        current += timedelta(days=1)
        await asyncio.sleep(1)  # Rate limit

    return total


async def backfill_greek_exposure(api_key: str, tickers: List[str]) -> int:
    """Backfill greek exposure for tickers (current snapshot only)."""
    connector = UWGreekExposureConnector(api_key)
    print(f"  Greek Exposure: {len(tickers)} tickers")
    count = await connector.fetch_and_store(tickers)
    return count


async def backfill_max_pain(api_key: str, tickers: List[str]) -> int:
    """Backfill max pain for tickers (current snapshot only)."""
    connector = UWMaxPainConnector(api_key)
    print(f"  Max Pain: {len(tickers)} tickers")
    count = await connector.fetch_and_store(tickers)
    return count


async def main():
    """Run historical backfill."""
    await init_db()

    api_key = os.environ.get("UW_API_KEY")
    if not api_key:
        print("ERROR: UW_API_KEY not set")
        return

    # Date range based on flow data
    start_date = date(2025, 12, 24)
    end_date = date(2025, 12, 30)

    print("=" * 50)
    print("Historical Feature Backfill")
    print(f"Date range: {start_date} to {end_date}")
    print("=" * 50)

    # Market Tide - has date parameter, can backfill
    print("\n[1/3] Backfilling Market Tide...")
    tide_count = await backfill_market_tide(api_key, start_date, end_date)
    print(f"  -> Stored {tide_count} market tide ticks")

    # Greek Exposure - current snapshot only
    print("\n[2/3] Fetching Greek Exposure (current snapshot)...")
    gex_count = await backfill_greek_exposure(api_key, TICKERS)
    print(f"  -> Stored {gex_count} greek exposure records")

    # Max Pain - current snapshot only
    print("\n[3/3] Fetching Max Pain (current snapshot)...")
    mp_count = await backfill_max_pain(api_key, TICKERS)
    print(f"  -> Stored {mp_count} max pain records")

    print("\n" + "=" * 50)
    print("Backfill Complete!")
    print(f"  Market Tide: {tide_count}")
    print(f"  Greek Exposure: {gex_count}")
    print(f"  Max Pain: {mp_count}")
    print("=" * 50)
    print("\nNote: GEX and Max Pain are point-in-time snapshots.")
    print("For full historical analysis, these would need daily collection.")


if __name__ == "__main__":
    asyncio.run(main())
