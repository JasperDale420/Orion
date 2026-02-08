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

import httpx
from orion.config import system_settings
from orion.core.logging_config import setup_logging
from orion.shared.db_utils import db_query
from sqlalchemy import text

logger = logging.getLogger("orion.jobs.sync_earnings")


def _gateway_base_url() -> str:
    base_url = (system_settings.data_gateway_url or "").strip()
    if not base_url:
        raise ValueError("DATA_GATEWAY_URL/GATEWAY_URL setting not configured")
    return base_url.rstrip("/")


def _gateway_headers() -> Dict[str, str]:
    api_key = (system_settings.data_gateway_api_key or "").strip()
    if not api_key:
        raise ValueError("DATA_GATEWAY_API_KEY/GATEWAY_API_KEY setting not configured")
    return {"X-Gateway-Key": api_key}


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_gateway_date(raw: Any) -> Optional[date]:
    if not raw:
        return None
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        from datetime import datetime as dt

        value = raw.strip()
        if not value:
            return None
        try:
            return dt.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


async def _fetch_gateway_earnings(endpoint: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    url = f"{_gateway_base_url()}{endpoint}"
    headers = _gateway_headers()
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()

    if not isinstance(payload, dict):
        return []

    data = payload.get("data", [])
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


async def sync_todays_earnings() -> Dict[str, int]:
    """Sync today's earnings via Data Gateway (premarket + afterhours)."""
    results = {"synced": 0, "errors": 0}
    today = date.today()

    # Fetch both premarket and afterhours earnings
    await _fetch_and_sync_earnings("/api/v1/uw/earnings/premarket", today, "premarket", results)
    await _fetch_and_sync_earnings("/api/v1/uw/earnings/afterhours", today, "afterhours", results)

    logger.info(f"Earnings sync complete: {results}")
    return results


async def _fetch_and_sync_earnings(
    endpoint: str, target_date: date, fallback_announce_time: str, results: Dict[str, int]
) -> None:
    """Fetch earnings from Data Gateway endpoint and sync to database."""
    try:
        rows = await _fetch_gateway_earnings(
            endpoint=endpoint,
            params={"date": target_date.isoformat(), "limit": 100},
        )
        for row in rows:
            try:
                await _upsert_earnings_row(row=row, fallback_announce_time=fallback_announce_time)
                results["synced"] += 1
            except Exception as ex:
                logger.debug(f"Failed to upsert earnings row: {ex}")
                results["errors"] += 1
    except Exception as e:
        logger.error(f"Failed to fetch earnings from {endpoint}: {e}")
        results["errors"] += 1


async def backfill_ticker_earnings(ticker: str) -> int:
    """Backfill historical earnings for a single ticker."""
    count = 0
    try:
        rows = await _fetch_gateway_earnings(
            endpoint=f"/api/v1/uw/earnings/{ticker.upper()}",
            params={"limit": 100},
        )
        for row in rows:
            count += await _process_single_earnings_record(ticker=ticker, row=row)
    except Exception as e:
        logger.debug(f"Failed to fetch earnings for {ticker}: {e}")

    return count


async def _process_single_earnings_record(ticker: str, row: Dict[str, Any]) -> int:
    """Process a single earnings record and upsert to database."""
    report_date = _parse_gateway_date(row.get("date") or row.get("report_date") or row.get("earnings_date"))
    if report_date is None:
        return 0

    try:
        announce = row.get("time") or row.get("announce_time") or row.get("report_time") or row.get("earnings_time")

        await _upsert_earnings_direct(
            ticker=ticker.upper(),
            report_date=report_date,
            announce_time=announce,
            eps_estimate=_to_float(row.get("eps_estimate") or row.get("street_mean_est") or row.get("eps_mean_est")),
            eps_actual=_to_float(row.get("eps_actual")),
            revenue_estimate=_to_float(row.get("revenue_estimate")),
            revenue_actual=_to_float(row.get("revenue_actual")),
        )
        return 1
    except Exception as ex:
        logger.debug(f"Failed to upsert earnings for {ticker}: {ex}")
        return 0


def _parse_report_date(report_date_raw: Any) -> date:
    """Parse report date from string or date object."""
    from datetime import datetime as dt

    if isinstance(report_date_raw, str):
        return dt.strptime(report_date_raw, "%Y-%m-%d").date()
    return report_date_raw


def _extract_announce_time(e: Any, UNSET: Any) -> Optional[str]:
    """Extract announce time from earnings record."""
    if hasattr(e, "additional_properties") and e.additional_properties:
        return e.additional_properties.get("report_time")

    if hasattr(e, "report_time") and e.report_time and not isinstance(e.report_time, type(UNSET)):
        return str(e.report_time.value) if hasattr(e.report_time, "value") else str(e.report_time)

    return None


def _extract_eps_estimate(e: Any, UNSET: Any) -> Optional[float]:
    """Extract EPS estimate from earnings record."""
    if not hasattr(e, "street_mean_est") or not e.street_mean_est:
        return None

    try:
        return float(e.street_mean_est) if not isinstance(e.street_mean_est, type(UNSET)) else None
    except (ValueError, TypeError):
        return None


async def backfill_all_earnings() -> Dict[str, int]:
    """Backfill earnings for all unique tickers via Data Gateway."""
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
            count = await backfill_ticker_earnings(ticker)
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


async def _upsert_earnings_row(row: Dict[str, Any], fallback_announce_time: str) -> None:
    ticker = (row.get("symbol") or row.get("ticker") or "").strip().upper()
    if not ticker:
        return

    report_date = _parse_gateway_date(row.get("date") or row.get("report_date") or row.get("earnings_date"))
    if report_date is None:
        return

    announce_time = (
        row.get("time")
        or row.get("announce_time")
        or row.get("report_time")
        or row.get("earnings_time")
        or fallback_announce_time
    )

    await _upsert_earnings_direct(
        ticker=ticker,
        report_date=report_date,
        announce_time=str(announce_time) if announce_time is not None else None,
        eps_estimate=_to_float(row.get("eps_estimate") or row.get("street_mean_est") or row.get("eps_mean_est")),
        eps_actual=_to_float(row.get("eps_actual")),
        revenue_estimate=_to_float(row.get("revenue_estimate")),
        revenue_actual=_to_float(row.get("revenue_actual")),
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

    setup_logging()

    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        print("Running earnings backfill...")
        result = asyncio.run(backfill_all_earnings())
        print(f"Result: {result}")
    else:
        print("Running daily earnings sync...")
        result = asyncio.run(sync_todays_earnings())
        print(f"Result: {result}")
