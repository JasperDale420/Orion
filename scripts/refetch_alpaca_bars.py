#!/usr/bin/env python3
"""
Re-fetch historical Alpaca bars to fix 0-valued OHLCV data.

This script fetches bars from Alpaca for a specified date range and updates
existing silver_alpaca_bars records that have close=0.

Usage:
    docker-compose run --rm price_target_labeler python scripts/refetch_alpaca_bars.py
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from dotenv import load_dotenv
load_dotenv()

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from sqlalchemy import text

from orion.shared.db_utils import db_query, db_write
from orion.storage.db import init_db


# Alpaca credentials
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")


async def get_tickers_needing_fix() -> List[str]:
    """Get unique tickers with 0-valued bars."""
    async def query(session: Any) -> List[str]:
        stmt = text("""
            SELECT DISTINCT ticker
            FROM silver_alpaca_bars
            WHERE close = 0 OR close IS NULL
            ORDER BY ticker
        """)
        result = await session.execute(stmt)
        return [row[0] for row in result.fetchall()]
    return await db_query(query)


async def get_dates_needing_fix() -> List[datetime]:
    """Get dates with 0-valued bars."""
    async def query(session: Any) -> List[datetime]:
        stmt = text("""
            SELECT DISTINCT DATE(bar_start_ts_utc) as bar_date
            FROM silver_alpaca_bars
            WHERE close = 0 OR close IS NULL
            ORDER BY bar_date
        """)
        result = await session.execute(stmt)
        return [row[0] for row in result.fetchall()]
    return await db_query(query)


def fetch_bars_from_alpaca(client: StockHistoricalDataClient, tickers: List[str], 
                           start: datetime, end: datetime) -> Dict[str, List[Dict]]:
    """Fetch bars from Alpaca API."""
    req = StockBarsRequest(
        symbol_or_symbols=tickers,
        timeframe=TimeFrame.Minute,
        start=start,
        end=end,
        limit=10000,
        feed="sip",
    )
    
    try:
        bar_set = client.get_stock_bars(req)
        results = {}
        
        for ticker, bars in bar_set.data.items():
            results[ticker] = []
            for bar in bars:
                try:
                    payload = bar.model_dump(mode="json")
                except AttributeError:
                    payload = bar.dict()
                
                # Extract values
                bar_data = {
                    "timestamp": bar.timestamp if hasattr(bar, "timestamp") else None,
                    "open": getattr(bar, "open", 0) or payload.get("open", 0) or payload.get("o", 0),
                    "high": getattr(bar, "high", 0) or payload.get("high", 0) or payload.get("h", 0),
                    "low": getattr(bar, "low", 0) or payload.get("low", 0) or payload.get("l", 0),
                    "close": getattr(bar, "close", 0) or payload.get("close", 0) or payload.get("c", 0),
                    "volume": getattr(bar, "volume", 0) or payload.get("volume", 0) or payload.get("v", 0),
                    "vwap": getattr(bar, "vwap", None) or payload.get("vwap") or payload.get("vw"),
                }
                
                if bar_data["close"] and bar_data["close"] > 0:
                    results[ticker].append(bar_data)
        
        return results
    except Exception as e:
        print(f"Error fetching bars: {e}")
        return {}


async def update_bar(ticker: str, bar_ts: datetime, ohlcv: Dict) -> bool:
    """Update a single bar with new OHLCV values."""
    async def do_update(session: Any) -> None:
        stmt = text("""
            UPDATE silver_alpaca_bars SET
                open = :open,
                high = :high,
                low = :low,
                close = :close,
                volume = :volume,
                vwap = :vwap
            WHERE ticker = :ticker
            AND bar_start_ts_utc = :bar_ts
            AND (close = 0 OR close IS NULL)
        """)
        await session.execute(stmt, {
            "ticker": ticker,
            "bar_ts": bar_ts,
            "open": ohlcv.get("open"),
            "high": ohlcv.get("high"),
            "low": ohlcv.get("low"),
            "close": ohlcv.get("close"),
            "volume": ohlcv.get("volume"),
            "vwap": ohlcv.get("vwap"),
        })
    
    try:
        await db_write(do_update)
        return True
    except Exception as e:
        print(f"Error updating {ticker} at {bar_ts}: {e}")
        return False


async def main():
    # Initialize database
    await init_db()
    
    # Initialize Alpaca client
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        print("ERROR: ALPACA_API_KEY and ALPACA_SECRET_KEY must be set")
        return
    
    client = StockHistoricalDataClient(api_key=ALPACA_API_KEY, secret_key=ALPACA_SECRET_KEY)
    
    # Get tickers and dates needing fix
    tickers = await get_tickers_needing_fix()
    dates = await get_dates_needing_fix()
    
    print(f"Found {len(tickers)} tickers with 0-valued bars")
    print(f"Found {len(dates)} dates with 0-valued bars: {dates}")
    
    if not tickers or not dates:
        print("No bars need fixing")
        return
    
    total_updated = 0
    total_fetched = 0
    
    # Process each date
    for bar_date in dates:
        start = datetime.combine(bar_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        
        print(f"\nProcessing {bar_date}...")
        
        # Fetch in batches of 100 tickers
        for i in range(0, len(tickers), 100):
            batch_tickers = tickers[i:i+100]
            print(f"  Fetching {len(batch_tickers)} tickers from Alpaca...")
            
            bars_data = fetch_bars_from_alpaca(client, batch_tickers, start, end)
            
            for ticker, bars in bars_data.items():
                for bar in bars:
                    if bar.get("close") and bar.get("close") > 0:
                        total_fetched += 1
                        success = await update_bar(ticker, bar["timestamp"], bar)
                        if success:
                            total_updated += 1
            
            print(f"  Fetched {total_fetched} valid bars, updated {total_updated}")
    
    print(f"\n=== COMPLETE ===")
    print(f"Total bars fetched from Alpaca: {total_fetched}")
    print(f"Total bars updated in database: {total_updated}")


if __name__ == "__main__":
    asyncio.run(main())
