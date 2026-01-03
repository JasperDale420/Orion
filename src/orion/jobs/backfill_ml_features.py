"""
Backfill script for price_target_labels ML feature columns.

Re-processes existing records to populate:
- Time features: entry_hour, entry_session, entry_day_of_week
- Greeks/flow: delta_at_entry, gamma_at_entry, volume_at_entry, open_interest_at_entry
- IV: iv_at_entry, iv_at_1h, iv_change_1h_pct
- Underlying: underlying_at_entry, underlying_at_1h, underlying_change_1h_pct
- Sector/Industry: sector, industry
- Earnings: days_to_earnings, is_post_earnings

Usage:
    python -m orion.jobs.backfill_ml_features [--batch-size 50] [--limit 1000]
"""

import argparse
import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from orion.shared.db_utils import db_query, db_write
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db
from orion.unusualwhales.client import UnusualWhalesClient
from orion.unusualwhales.api.stock import get_info
from orion.unusualwhales.models.ticker_info_results import TickerInfoResults

logger = setup_struct_logger("orion.backfill.ml_features")

BATCH_SIZE = 50

# Ticker info cache
_ticker_info_cache: Dict[str, Dict[str, Any]] = {}
_uw_client: Optional[UnusualWhalesClient] = None


def _get_uw_client() -> Optional[UnusualWhalesClient]:
    """Get or create UW client."""
    global _uw_client
    if _uw_client is None:
        api_key = os.getenv("UW_API_KEY")
        base_url = os.getenv("UW_BASE_URL", "https://api.unusualwhales.com")
        if not api_key:
            logger.warning("UW_API_KEY not set, ticker info lookups will fail")
            return None
        _uw_client = UnusualWhalesClient(base_url=base_url, token=api_key)
    return _uw_client


async def get_ticker_info(ticker: str) -> Dict[str, Any]:
    """Fetch ticker info from UW API with caching."""
    if ticker in _ticker_info_cache:
        return _ticker_info_cache[ticker]
    
    cache_entry = {
        "sector": None,
        "next_earnings_date": None,
        "announce_time": None,
    }
    
    client = _get_uw_client()
    if client is None:
        _ticker_info_cache[ticker] = cache_entry
        return cache_entry
    
    try:
        response = await asyncio.to_thread(
            get_info.sync,
            ticker=ticker,
            client=client,
        )
        
        if isinstance(response, TickerInfoResults) and response.data:
            info = response.data
            from orion.unusualwhales.types import UNSET
            
            cache_entry["sector"] = (
                info.sector.value if info.sector and not isinstance(info.sector, type(UNSET)) else None
            )
            cache_entry["next_earnings_date"] = (
                info.next_earnings_date if info.next_earnings_date and not isinstance(info.next_earnings_date, type(UNSET)) else None
            )
            
    except Exception as e:
        logger.debug(f"Failed to fetch ticker info for {ticker}: {e}")
    
    _ticker_info_cache[ticker] = cache_entry
    return cache_entry


def get_entry_time_features(entry_ts: datetime) -> Dict[str, Any]:
    """Extract time-based features from entry timestamp."""
    hour = entry_ts.hour
    day_of_week = entry_ts.weekday()
    
    if hour < 10:
        session = "early"
    elif hour < 12:
        session = "midday"
    elif hour < 14:
        session = "afternoon"
    else:
        session = "late"
    
    return {
        "entry_hour": hour,
        "entry_session": session,
        "entry_day_of_week": day_of_week,
    }


async def get_flow_greeks(event_id: str) -> Dict[str, Optional[float]]:
    """Get volume, OI, and IV from flow data."""
    async def query(session: Any) -> Dict[str, Optional[float]]:
        stmt = text("""
            SELECT volume_contract, open_interest, iv, delta_diff
            FROM silver_uw_flow
            WHERE event_id = :event_id
        """)
        result = await session.execute(stmt, {"event_id": event_id})
        row = result.fetchone()
        if row:
            return {
                "delta": row[3],
                "gamma": None,
                "volume": row[0],
                "open_interest": row[1],
                "iv": row[2],
            }
        return {"delta": None, "gamma": None, "volume": None, "open_interest": None, "iv": None}
    
    return await db_query(query)


async def get_underlying_price_at_entry(ticker: str, entry_ts: datetime) -> Optional[float]:
    """Get underlying stock price at entry time from bars."""
    async def query(session: Any) -> Optional[float]:
        stmt = text("""
            SELECT close
            FROM silver_alpaca_bars
            WHERE ticker = :ticker
            AND bar_start_ts_utc <= :entry_ts
            ORDER BY bar_start_ts_utc DESC
            LIMIT 1
        """)
        result = await session.execute(stmt, {"ticker": ticker, "entry_ts": entry_ts})
        row = result.fetchone()
        return row[0] if row else None
    
    return await db_query(query)


async def get_underlying_price_at_offset(ticker: str, entry_ts: datetime, hours: int) -> Optional[float]:
    """Get underlying stock price at offset from entry."""
    target_ts = entry_ts + timedelta(hours=hours)
    async def query(session: Any) -> Optional[float]:
        stmt = text("""
            SELECT close
            FROM silver_alpaca_bars
            WHERE ticker = :ticker
            AND bar_start_ts_utc <= :target_ts
            ORDER BY bar_start_ts_utc DESC
            LIMIT 1
        """)
        result = await session.execute(stmt, {"ticker": ticker, "target_ts": target_ts})
        row = result.fetchone()
        return row[0] if row else None
    
    return await db_query(query)


async def get_records_to_backfill(limit: int = 1000) -> List[Dict[str, Any]]:
    """Get records missing ML feature columns."""
    async def query(session: Any) -> List[Dict[str, Any]]:
        stmt = text("""
            SELECT event_id, ticker, entry_ts
            FROM price_target_labels
            WHERE entry_hour IS NULL
            LIMIT :limit
        """)
        result = await session.execute(stmt, {"limit": limit})
        rows = result.fetchall()
        return [dict(row._mapping) for row in rows]
    
    return await db_query(query)


async def update_ml_features(record: Dict[str, Any]) -> bool:
    """Update ML feature columns for a record."""
    event_id = record["event_id"]
    ticker = record["ticker"]
    entry_ts = record["entry_ts"]
    
    updates: Dict[str, Any] = {}
    
    # Time features
    time_features = get_entry_time_features(entry_ts)
    updates.update(time_features)
    
    # Greeks/flow data
    greeks = await get_flow_greeks(event_id)
    updates["delta_at_entry"] = greeks.get("delta")
    updates["gamma_at_entry"] = greeks.get("gamma")
    updates["volume_at_entry"] = greeks.get("volume")
    updates["open_interest_at_entry"] = greeks.get("open_interest")
    updates["iv_at_entry"] = greeks.get("iv")
    updates["iv_at_1h"] = None
    updates["iv_change_1h_pct"] = None
    
    # Underlying price
    underlying_entry = await get_underlying_price_at_entry(ticker, entry_ts)
    underlying_1h = await get_underlying_price_at_offset(ticker, entry_ts, 1)
    underlying_change = None
    if underlying_entry and underlying_1h and underlying_entry > 0:
        underlying_change = ((underlying_1h - underlying_entry) / underlying_entry) * 100
    updates["underlying_at_entry"] = underlying_entry
    updates["underlying_at_1h"] = underlying_1h
    updates["underlying_change_1h_pct"] = underlying_change
    
    # Sector/earnings from UW
    ticker_info = await get_ticker_info(ticker)
    updates["sector"] = ticker_info.get("sector")
    updates["industry"] = None
    
    next_earnings = ticker_info.get("next_earnings_date")
    if next_earnings:
        entry_date = entry_ts.date()
        days_diff = (next_earnings - entry_date).days
        if days_diff < 0:
            updates["days_to_earnings"] = None
            updates["is_post_earnings"] = True
        else:
            updates["days_to_earnings"] = days_diff
            updates["is_post_earnings"] = False
    else:
        updates["days_to_earnings"] = None
        updates["is_post_earnings"] = None
    
    # Build update query
    set_clauses = []
    for k in updates:
        set_clauses.append(f"{k} = :{k}")
    
    query = f"UPDATE price_target_labels SET {', '.join(set_clauses)} WHERE event_id = :event_id"
    updates["event_id"] = event_id
    
    async def write(session: Any) -> None:
        await session.execute(text(query), updates)
    
    await db_write(write)
    return True


async def run_backfill(batch_size: int = BATCH_SIZE, limit: int = 1000) -> None:
    """Run the backfill job."""
    await init_db()
    
    logger.info(f"Starting ML features backfill with batch_size={batch_size}, limit={limit}")
    
    total_processed = 0
    total_updated = 0
    
    while True:
        records = await get_records_to_backfill(limit=batch_size)
        if not records:
            break
        
        for record in records:
            try:
                if await update_ml_features(record):
                    total_updated += 1
            except Exception as e:
                logger.error(f"Failed to update {record['event_id']}: {e}")
            
            total_processed += 1
            
            if total_processed >= limit:
                break
        
        logger.info(
            f"Processed {total_processed} records | Updated: {total_updated} | "
            f"Tickers cached: {len(_ticker_info_cache)}"
        )
        
        if total_processed >= limit:
            break
        
        # Rate limiting for UW API
        await asyncio.sleep(0.5)
    
    logger.info(f"Backfill complete! Processed: {total_processed}, Updated: {total_updated}")


async def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Backfill ML feature columns")
    parser.add_argument("--batch-size", type=int, default=50, help="Batch size for processing")
    parser.add_argument("--limit", type=int, default=1000, help="Max records to process")
    args = parser.parse_args()
    
    await run_backfill(batch_size=args.batch_size, limit=args.limit)


if __name__ == "__main__":
    asyncio.run(main())
