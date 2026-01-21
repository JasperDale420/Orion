"""
Sync and backfill earnings calendar from UW API.

This job:
1. Syncs today's upcoming earnings (premarket/afterhours) - run daily
2. Backfills historical earnings for all tickers - run once
"""

import asyncio
import logging
from datetime import date
from typing import Any, Dict, List, Optional

from orion.shared.db_utils import db_query
from sqlalchemy import text

logger = logging.getLogger("orion.jobs.sync_earnings")


async def sync_todays_earnings() -> Dict[str, int]:
    """Sync today's earnings via Data Gateway (premarket + afterhours)."""
    import os

    from orion.unusualwhales.api.earnings import get_afterhours_earnings, get_premarket_earnings
    from orion.unusualwhales.client import UnusualWhalesClient

    gateway_url = os.getenv("GATEWAY_URL", "http://localhost:8080")
    client = UnusualWhalesClient(base_url=f"{gateway_url}/api/v1/uw", token="gateway")
    results = {"synced": 0, "errors": 0}
    today = date.today()

    # Fetch both premarket and afterhours earnings
    await _fetch_and_sync_earnings(get_premarket_earnings.sync, client, today, "premarket", results)
    await _fetch_and_sync_earnings(get_afterhours_earnings.sync, client, today, "afterhours", results)

    logger.info(f"Earnings sync complete: {results}")
    return results


async def _fetch_and_sync_earnings(
    fetch_fn: Any, client: Any, today: date, announce_time: str, results: Dict[str, int]
) -> None:
    """Fetch earnings using the given function and sync to database."""
    from orion.unusualwhales.models.earnings_results import EarningsResults

    try:
        response = await asyncio.to_thread(fetch_fn, client=client)
        if isinstance(response, EarningsResults) and response.data:
            for e in response.data:
                try:
                    await _upsert_earnings(e, today, announce_time)
                    results["synced"] += 1
                except Exception as ex:
                    logger.debug(f"Failed to upsert earnings: {ex}")
                    results["errors"] += 1
    except Exception as e:
        logger.error(f"Failed to fetch {announce_time} earnings: {e}")
        results["errors"] += 1


async def backfill_ticker_earnings(ticker: str, client: Any) -> int:
    """Backfill historical earnings for a single ticker."""
    from datetime import datetime as dt

    from orion.unusualwhales.api.earnings import get_ticker_earnings
    from orion.unusualwhales.models.earnings_results import EarningsResults
    from orion.unusualwhales.types import UNSET

    count = 0
    try:
        response = await asyncio.to_thread(get_ticker_earnings.sync, ticker=ticker, client=client)
        if isinstance(response, EarningsResults) and response.data:
            for e in response.data:
                report_date_raw = e.report_date
                if report_date_raw and not isinstance(report_date_raw, type(UNSET)):
                    try:
                        # Parse string date to date object
                        if isinstance(report_date_raw, str):
                            report_date = dt.strptime(report_date_raw, "%Y-%m-%d").date()
                        else:
                            report_date = report_date_raw

                        # Get announce time from additional_properties
                        announce = None
                        if hasattr(e, "additional_properties") and e.additional_properties:
                            announce = e.additional_properties.get("report_time")
                        elif hasattr(e, "report_time") and e.report_time and not isinstance(e.report_time, type(UNSET)):
                            announce = (
                                str(e.report_time.value) if hasattr(e.report_time, "value") else str(e.report_time)
                            )

                        # Get EPS from street_mean_est (it's the estimate)
                        eps_est = None
                        if hasattr(e, "street_mean_est") and e.street_mean_est:
                            try:
                                eps_est = (
                                    float(e.street_mean_est) if not isinstance(e.street_mean_est, type(UNSET)) else None
                                )
                            except (ValueError, TypeError):
                                pass

                        await _upsert_earnings_direct(
                            ticker=ticker,
                            report_date=report_date,
                            announce_time=announce,
                            eps_estimate=eps_est,
                            eps_actual=getattr(e, "eps_actual", None),
                            revenue_estimate=getattr(e, "revenue_estimate", None),
                            revenue_actual=getattr(e, "revenue_actual", None),
                        )
                        count += 1
                    except Exception as ex:
                        logger.debug(f"Failed to upsert earnings for {ticker}: {ex}")
    except Exception as e:
        logger.debug(f"Failed to fetch earnings for {ticker}: {e}")

    return count


async def backfill_all_earnings() -> Dict[str, int]:
    """Backfill earnings for all unique tickers via Data Gateway."""
    import os

    from orion.unusualwhales.client import UnusualWhalesClient

    gateway_url = os.getenv("GATEWAY_URL", "http://localhost:8080")
    # Use gateway URL with a placeholder token (auth handled by Gateway)
    client = UnusualWhalesClient(base_url=f"{gateway_url}/api/v1/uw", token="gateway")
    results = {"tickers": 0, "earnings": 0, "errors": 0}

    # Get unique tickers from labels
    async def get_tickers(session: Any) -> List[str]:
        stmt = text("SELECT DISTINCT ticker FROM price_target_labels ORDER BY ticker")
        result = await session.execute(stmt)
        return [row[0] for row in result.fetchall()]

    tickers = await db_query(get_tickers)
    logger.info(f"Backfilling earnings for {len(tickers)} tickers")

    for i, ticker in enumerate(tickers):
        try:
            count = await backfill_ticker_earnings(ticker, client)
            results["earnings"] += count
            results["tickers"] += 1
            if (i + 1) % 50 == 0:
                logger.info(f"Progress: {i + 1}/{len(tickers)} tickers, {results['earnings']} earnings")
            # Rate limit: 1 request per 100ms
            await asyncio.sleep(0.5)  # Rate limit: 2 requests per second
        except Exception as e:
            logger.debug(f"Failed to backfill {ticker}: {e}")
            results["errors"] += 1

    logger.info(f"Earnings backfill complete: {results}")
    return results


async def _upsert_earnings(earnings_obj: Any, report_date: date, announce_time: str) -> None:
    """Upsert a single earnings record from API response object."""
    from orion.unusualwhales.types import UNSET

    ticker = getattr(earnings_obj, "ticker", None) or getattr(earnings_obj, "symbol", None)
    if not ticker or isinstance(ticker, type(UNSET)):
        return

    await _upsert_earnings_direct(
        ticker=str(ticker),
        report_date=report_date,
        announce_time=announce_time,
        eps_estimate=getattr(earnings_obj, "eps_estimate", None),
        eps_actual=getattr(earnings_obj, "eps_actual", None),
        revenue_estimate=getattr(earnings_obj, "revenue_estimate", None),
        revenue_actual=getattr(earnings_obj, "revenue_actual", None),
    )


async def _upsert_earnings_direct(
    ticker: str,
    report_date: date,
    announce_time: Optional[str] = None,
    eps_estimate: Optional[float] = None,
    eps_actual: Optional[float] = None,
    revenue_estimate: Optional[int] = None,
    revenue_actual: Optional[int] = None,
) -> None:
    """Upsert earnings record directly."""
    from orion.unusualwhales.types import UNSET

    # Clean up UNSET values
    if isinstance(eps_estimate, type(UNSET)):
        eps_estimate = None
    if isinstance(eps_actual, type(UNSET)):
        eps_actual = None
    if isinstance(revenue_estimate, type(UNSET)):
        revenue_estimate = None
    if isinstance(revenue_actual, type(UNSET)):
        revenue_actual = None

    async def upsert(session: Any) -> None:
        stmt = text(
            """
            INSERT INTO silver_earnings_calendar
                (ticker, report_date, announce_time, eps_estimate, eps_actual, revenue_estimate, revenue_actual, updated_at_utc)
            VALUES
                (:ticker, :report_date, :announce_time, :eps_estimate, :eps_actual, :revenue_estimate, :revenue_actual, NOW())
            ON CONFLICT (ticker, report_date)
            DO UPDATE SET
                announce_time = COALESCE(EXCLUDED.announce_time, silver_earnings_calendar.announce_time),
                eps_estimate = COALESCE(EXCLUDED.eps_estimate, silver_earnings_calendar.eps_estimate),
                eps_actual = COALESCE(EXCLUDED.eps_actual, silver_earnings_calendar.eps_actual),
                revenue_estimate = COALESCE(EXCLUDED.revenue_estimate, silver_earnings_calendar.revenue_estimate),
                revenue_actual = COALESCE(EXCLUDED.revenue_actual, silver_earnings_calendar.revenue_actual),
                updated_at_utc = NOW()
        """
        )
        await session.execute(
            stmt,
            {
                "ticker": ticker,
                "report_date": report_date,
                "announce_time": announce_time,
                "eps_estimate": eps_estimate,
                "eps_actual": eps_actual,
                "revenue_estimate": revenue_estimate,
                "revenue_actual": revenue_actual,
            },
        )

    from orion.shared.db_utils import db_write

    await db_write(upsert)


async def get_earnings_for_ticker(ticker: str, as_of_date: date) -> Dict[str, Any]:
    """Get earnings info for a ticker as of a specific date.

    Returns:
        {
            "days_to_earnings": int or None (days until next earnings),
            "is_post_earnings": bool (within 5 days after earnings),
            "next_earnings_date": date or None,
            "last_earnings_date": date or None,
        }
    """

    async def query(session: Any) -> Dict[str, Any]:
        # Get next earnings (future)
        next_stmt = text(
            """
            SELECT report_date, announce_time FROM silver_earnings_calendar
            WHERE ticker = :ticker AND report_date >= :as_of
            ORDER BY report_date ASC LIMIT 1
        """
        )
        next_result = await session.execute(next_stmt, {"ticker": ticker, "as_of": as_of_date})
        next_row = next_result.fetchone()

        # Get last earnings (past)
        last_stmt = text(
            """
            SELECT report_date, announce_time FROM silver_earnings_calendar
            WHERE ticker = :ticker AND report_date < :as_of
            ORDER BY report_date DESC LIMIT 1
        """
        )
        last_result = await session.execute(last_stmt, {"ticker": ticker, "as_of": as_of_date})
        last_row = last_result.fetchone()

        result = {
            "days_to_earnings": None,
            "is_post_earnings": False,
            "next_earnings_date": None,
            "last_earnings_date": None,
        }

        if next_row:
            result["next_earnings_date"] = next_row[0]
            result["days_to_earnings"] = (next_row[0] - as_of_date).days

        if last_row:
            result["last_earnings_date"] = last_row[0]
            days_since = (as_of_date - last_row[0]).days
            result["is_post_earnings"] = 0 <= days_since <= 5

        return result

    return await db_query(query)


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        print("Running earnings backfill...")
        result = asyncio.run(backfill_all_earnings())
        print(f"Result: {result}")
    else:
        print("Running daily earnings sync...")
        result = asyncio.run(sync_todays_earnings())
        print(f"Result: {result}")
