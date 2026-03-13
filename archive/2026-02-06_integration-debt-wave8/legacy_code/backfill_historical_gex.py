"""
Historical GEX Backfill Job.

Fetches historical greek exposure (GEX/VEX) data from Unusual Whales API
for dates where we have price_target_labels but no source data.

Usage:
    python -m orion.jobs.backfill_historical_gex
"""

import asyncio
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

import requests
from orion.shared.db_utils import db_query, db_write
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential

logger = setup_struct_logger("orion.backfill_historical_gex")

BASE_URL = "https://api.unusualwhales.com"


async def get_dates_needing_gex() -> list[date]:
    """Find trading dates where we have labels but no GEX data."""

    async def query(session: Any) -> list[Any]:
        stmt = text("""
            SELECT DISTINCT DATE(entry_ts) as trading_date
            FROM price_target_labels
            WHERE ml_ready = true
            ORDER BY trading_date
        """)
        result = await session.execute(stmt)
        return result.fetchall()

    label_dates = await db_query(query)

    async def query_gex_dates(session: Any) -> list[Any]:
        stmt = text("""
            SELECT DISTINCT DATE(ts_utc) as trading_date
            FROM silver_greek_exposure
        """)
        result = await session.execute(stmt)
        return result.fetchall()

    gex_dates = await db_query(query_gex_dates)
    gex_date_set = {row[0] for row in gex_dates}

    missing_dates = []
    for row in label_dates:
        if row[0] not in gex_date_set:
            missing_dates.append(row[0])

    return missing_dates


async def get_tickers_for_date(trading_date: date) -> set[str]:
    """Get unique underlying tickers from labels for a specific date."""

    async def query(session: Any) -> list[Any]:
        start_ts = datetime.combine(trading_date, datetime.min.time()).replace(tzinfo=UTC)
        end_ts = start_ts + timedelta(days=1)

        stmt = text("""
            SELECT DISTINCT ticker
            FROM price_target_labels
            WHERE entry_ts >= :start_ts
            AND entry_ts < :end_ts
            AND ticker IS NOT NULL
        """)
        result = await session.execute(stmt, {"start_ts": start_ts, "end_ts": end_ts})
        return result.fetchall()

    rows = await db_query(query)

    # Extract underlying ticker from option symbol (e.g., "AAPL250117C00150000" -> "AAPL")
    tickers = set()
    for row in rows:
        ticker = row[0]
        underlying = ""
        for c in ticker:
            if c.isalpha():
                underlying += c
            else:
                break
        if underlying:
            tickers.add(underlying)

    return tickers


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def fetch_gex_for_date(ticker: str, trading_date: date, api_key: str) -> dict[str, Any] | None:
    """Fetch GEX for a ticker on a specific historical date."""
    date_str = trading_date.strftime("%Y-%m-%d")
    url = f"{BASE_URL}/api/stock/{ticker}/greek-exposure"
    headers = {"Authorization": f"Bearer {api_key}"}
    params = {"date": date_str}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"Failed to fetch GEX for {ticker} on {date_str}: {e}")
        return None


async def store_gex_data(ticker: str, trading_date: date, data: dict[str, Any]) -> bool:
    """Store GEX data in silver_greek_exposure table."""

    exposure_data = data.get("data")
    if not exposure_data:
        return False

    # Handle list response (multiple dates) or single dict
    # API returns: call_gamma, put_gamma, call_vanna, put_vanna, call_charm, put_charm
    # GEX = call_gamma + put_gamma (net gamma exposure)
    # VEX = call_vanna + put_vanna (net vanna exposure)
    if isinstance(exposure_data, list):
        if not exposure_data:
            return False
        # Sum up all dates for aggregate exposure
        total_gex_oi = sum(
            float(e.get("call_gamma") or 0) + float(e.get("put_gamma") or 0)
            for e in exposure_data
        )
        total_gex_vol = 0
        total_vex_oi = sum(
            float(e.get("call_vanna") or 0) + float(e.get("put_vanna") or 0)
            for e in exposure_data
        )
        total_vex_vol = 0
        total_cex_oi = sum(
            float(e.get("call_charm") or 0) + float(e.get("put_charm") or 0)
            for e in exposure_data
        )
        total_cex_vol = 0
        call_delta = sum(float(e.get("call_delta") or 0) for e in exposure_data)
        put_delta = sum(float(e.get("put_delta") or 0) for e in exposure_data)
        spot = 0
    else:
        # Single dict response
        total_gex_oi = float(exposure_data.get("call_gamma") or 0) + float(exposure_data.get("put_gamma") or 0)
        total_gex_vol = 0
        total_vex_oi = float(exposure_data.get("call_vanna") or 0) + float(exposure_data.get("put_vanna") or 0)
        total_vex_vol = 0
        total_cex_oi = float(exposure_data.get("call_charm") or 0) + float(exposure_data.get("put_charm") or 0)
        total_cex_vol = 0
        call_delta = float(exposure_data.get("call_delta") or 0)
        put_delta = float(exposure_data.get("put_delta") or 0)
        spot = 0

    # Use midday timestamp for the historical record
    ts_utc = datetime.combine(trading_date, datetime.min.time()).replace(hour=12, tzinfo=UTC)

    async def write(session: Any) -> None:
        stmt = text("""
            INSERT INTO silver_greek_exposure (
                ticker, ts_utc, gex_oi, gex_volume,
                vex_oi, vex_volume, cex_oi, cex_volume,
                call_delta, put_delta, call_fill_delta, put_fill_delta,
                spot_price
            ) VALUES (
                :ticker, :ts_utc, :gex_oi, :gex_volume,
                :vex_oi, :vex_volume, :cex_oi, :cex_volume,
                :call_delta, :put_delta, 0, 0,
                :spot_price
            )
            ON CONFLICT DO NOTHING
        """)
        await session.execute(stmt, {
            "ticker": ticker,
            "ts_utc": ts_utc,
            "gex_oi": total_gex_oi,
            "gex_volume": total_gex_vol,
            "vex_oi": total_vex_oi,
            "vex_volume": total_vex_vol,
            "cex_oi": total_cex_oi,
            "cex_volume": total_cex_vol,
            "call_delta": call_delta,
            "put_delta": put_delta,
            "spot_price": spot,
        })

    await db_write(write)
    logger.info(f"Stored GEX for {ticker} on {trading_date}: gex_oi={total_gex_oi:.2f}")
    return True


async def run_backfill() -> dict[str, Any]:
    """Run the historical GEX backfill."""

    await init_db()

    # Get API key from environment
    api_key = os.getenv("UW_API_KEY")
    if not api_key:
        logger.error("UW_API_KEY not set")
        return {"error": "UW_API_KEY not set", "dates_processed": 0}

    # Get dates needing backfill
    missing_dates = await get_dates_needing_gex()

    if not missing_dates:
        logger.info("No dates need GEX backfill")
        return {"dates_processed": 0, "tickers_fetched": 0, "success_count": 0}

    logger.info(f"Found {len(missing_dates)} dates needing GEX backfill: {missing_dates}")

    total_fetched = 0
    total_success = 0

    for trading_date in missing_dates:
        tickers = await get_tickers_for_date(trading_date)
        logger.info(f"Processing {trading_date} with {len(tickers)} tickers")

        for ticker in tickers:
            # Fetch from API (runs in thread pool)
            data = await asyncio.to_thread(fetch_gex_for_date, ticker, trading_date, api_key)
            total_fetched += 1

            if data:
                success = await store_gex_data(ticker, trading_date, data)
                if success:
                    total_success += 1

            # Rate limiting
            await asyncio.sleep(0.5)

        logger.info(f"Completed {trading_date}: {total_success}/{total_fetched} successful")

    summary = {
        "dates_processed": len(missing_dates),
        "tickers_fetched": total_fetched,
        "success_count": total_success,
    }

    logger.info(f"Backfill complete: {summary}")
    return summary


async def main() -> None:
    await run_backfill()


if __name__ == "__main__":
    asyncio.run(main())
