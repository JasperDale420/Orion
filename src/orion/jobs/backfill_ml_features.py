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
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from orion.main_price_target_labeler import (
    get_entry_time_features as get_labeler_entry_time_features,
    get_gex_at_entry,
    get_max_pain_distance,
)
from orion.shared.db_utils import db_query, db_write
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db
from orion.unusualwhales.api.stock import get_info
from orion.unusualwhales.client import UnusualWhalesClient
from orion.unusualwhales.models.ticker_info_results import TickerInfoResults

logger = setup_struct_logger("orion.backfill.ml_features")

BATCH_SIZE = 50


def extract_underlying_ticker(option_symbol: str) -> str:
    """Extract underlying ticker from option symbol.

    Examples:
        QQQ250117C00450000 -> QQQ
        SPXW250117P05000000 -> SPXW
        AAPL250117C00150000 -> AAPL
    """
    underlying = ""
    for c in option_symbol:
        if c.isalpha():
            underlying += c
        else:
            break
    return underlying if underlying else option_symbol


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
                info.next_earnings_date
                if info.next_earnings_date and not isinstance(info.next_earnings_date, type(UNSET))
                else None
            )

    except Exception as e:
        logger.debug(f"Failed to fetch ticker info for {ticker}: {e}")

    _ticker_info_cache[ticker] = cache_entry
    return cache_entry


def get_entry_time_features(entry_ts: datetime) -> Dict[str, Any]:
    """Delegate to live labeler logic to keep backfill semantics aligned."""
    return get_labeler_entry_time_features(entry_ts)


async def get_flow_greeks(event_id: str) -> Dict[str, Optional[float]]:
    """Get volume, OI, and IV from flow data."""

    async def query(session: Any) -> Dict[str, Optional[float]]:
        stmt = text(
            """
            SELECT volume_contract, open_interest, iv, delta_diff
            FROM silver_uw_flow
            WHERE event_id = :event_id
        """
        )
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
        stmt = text(
            """
            SELECT close
            FROM silver_alpaca_bars
            WHERE ticker = :ticker
            AND bar_start_ts_utc <= :entry_ts
            ORDER BY bar_start_ts_utc DESC
            LIMIT 1
        """
        )
        result = await session.execute(stmt, {"ticker": ticker, "entry_ts": entry_ts})
        row = result.fetchone()
        return row[0] if row else None

    return await db_query(query)


async def get_underlying_price_at_offset(ticker: str, entry_ts: datetime, hours: int) -> Optional[float]:
    """Get underlying stock price at offset from entry."""
    target_ts = entry_ts + timedelta(hours=hours)

    async def query(session: Any) -> Optional[float]:
        stmt = text(
            """
            SELECT close
            FROM silver_alpaca_bars
            WHERE ticker = :ticker
            AND bar_start_ts_utc <= :target_ts
            ORDER BY bar_start_ts_utc DESC
            LIMIT 1
        """
        )
        result = await session.execute(stmt, {"ticker": ticker, "target_ts": target_ts})
        row = result.fetchone()
        return row[0] if row else None

    return await db_query(query)


async def get_phase1_features(ticker: str, entry_ts: datetime) -> Dict[str, Any]:
    """Get overnight gap and VWAP distance features from bars data."""
    result: Dict[str, Any] = {
        "overnight_gap_pct": None,
        "vwap_distance_pct": None,
        "minutes_to_close": None,
    }

    # Minutes to close (4pm ET = 20:00 UTC)
    market_close = entry_ts.replace(hour=20, minute=0, second=0, microsecond=0)
    if entry_ts < market_close:
        result["minutes_to_close"] = int((market_close - entry_ts).total_seconds() / 60)
    else:
        result["minutes_to_close"] = 0

    entry_date = entry_ts.date()

    async def query(session: Any) -> Dict[str, Any]:
        # Today's open for overnight gap
        today_stmt = text(
            """
            SELECT open
            FROM silver_alpaca_bars
            WHERE ticker = :ticker AND DATE(bar_start_ts_utc) = :entry_date
            ORDER BY bar_start_ts_utc ASC LIMIT 1
        """
        )
        today_result = await session.execute(today_stmt, {"ticker": ticker, "entry_date": entry_date})
        today_row = today_result.fetchone()
        today_open = today_row[0] if today_row else None

        # Prior trading day close (handles holidays/weekends)
        prior_stmt = text(
            """
            SELECT close
            FROM silver_alpaca_bars
            WHERE ticker = :ticker AND DATE(bar_start_ts_utc) < :entry_date
            ORDER BY bar_start_ts_utc DESC LIMIT 1
        """
        )
        prior_result = await session.execute(prior_stmt, {"ticker": ticker, "entry_date": entry_date})
        prior_row = prior_result.fetchone()
        prior_close = prior_row[0] if prior_row else None

        # Overnight gap
        if today_open and prior_close and prior_close > 0:
            result["overnight_gap_pct"] = ((today_open - prior_close) / prior_close) * 100

        # VWAP distance - bar closest to entry time
        vwap_stmt = text(
            """
            SELECT close, vwap
            FROM silver_alpaca_bars
            WHERE ticker = :ticker AND bar_start_ts_utc <= :entry_ts
            ORDER BY bar_start_ts_utc DESC LIMIT 1
        """
        )
        vwap_result = await session.execute(vwap_stmt, {"ticker": ticker, "entry_ts": entry_ts})
        vwap_row = vwap_result.fetchone()
        if vwap_row and vwap_row[0] and vwap_row[1] and vwap_row[1] > 0:
            result["vwap_distance_pct"] = ((vwap_row[0] - vwap_row[1]) / vwap_row[1]) * 100

        return result

    return await db_query(query)


async def get_records_to_backfill(limit: int = 1000) -> List[Dict[str, Any]]:
    """Get records missing ML feature columns."""

    async def query(session: Any) -> List[Dict[str, Any]]:
        stmt = text(
            """
            SELECT p.event_id, p.ticker, p.entry_ts, p.expiry, p.dte, f.option_chain
            FROM price_target_labels p
            LEFT JOIN silver_uw_flow f ON p.event_id = f.event_id
            WHERE p.entry_hour IS NULL OR p.overnight_gap_pct IS NULL OR p.gex_at_entry IS NULL
               OR p.oi_change_1d IS NULL
            ORDER BY p.entry_ts ASC, p.event_id ASC
            LIMIT :limit
        """
        )
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

    # Phase1 features: overnight gap, VWAP, minutes_to_close, price_change_5d, earnings_in_dte
    from orion.main_price_target_labeler import get_phase1_bucket_features

    dte = record.get("dte", 0) or 0
    phase1_updates = await get_phase1_bucket_features(ticker, entry_ts, dte)
    updates["overnight_gap_pct"] = phase1_updates.get("overnight_gap_pct")
    updates["vwap_distance_pct"] = phase1_updates.get("vwap_distance_pct")
    updates["minutes_to_close"] = phase1_updates.get("minutes_to_close")
    updates["price_change_5d_prior"] = phase1_updates.get("price_change_5d_prior")
    updates["earnings_in_dte_window"] = phase1_updates.get("earnings_in_dte_window")

    # GEX and max pain - use underlying ticker, not option symbol
    underlying = extract_underlying_ticker(ticker)
    gex_data = await get_gex_at_entry(underlying, entry_ts)
    updates["gex_at_entry"] = gex_data.get("gex")
    updates["vex_at_entry"] = gex_data.get("vex")

    max_pain = await get_max_pain_distance(ticker, record.get("expiry"), entry_ts)
    updates["max_pain_distance_pct"] = max_pain

    # Darkpool metrics for all windows
    from orion.main_price_target_labeler import (
        get_darkpool_metrics,
        get_flow_aggression,
        get_institutional_flow_1w,
        get_market_tide_before_entry,
        get_regime_at_entry,
        get_rvol_metrics,
    )

    dp_metrics = await get_darkpool_metrics(ticker, entry_ts)
    updates["darkpool_volume_1h"] = dp_metrics.get("darkpool_1h")
    updates["darkpool_15m"] = dp_metrics.get("darkpool_15m")
    updates["darkpool_30m"] = dp_metrics.get("darkpool_30m")
    updates["darkpool_4h"] = dp_metrics.get("darkpool_4h")
    updates["darkpool_1d"] = dp_metrics.get("darkpool_1d")
    updates["darkpool_3d"] = dp_metrics.get("darkpool_3d")
    updates["darkpool_1w"] = dp_metrics.get("darkpool_1w")
    updates["darkpool_2w"] = dp_metrics.get("darkpool_2w")
    updates["darkpool_4w"] = dp_metrics.get("darkpool_4w")

    # RVOL metrics
    rvol = await get_rvol_metrics(ticker, entry_ts)
    updates["rvol_1h"] = rvol.get("rvol_1h")
    updates["rvol_daily"] = rvol.get("rvol_daily")
    updates["rvol_weekly"] = rvol.get("rvol_weekly")
    updates["rvol_30m"] = rvol.get("rvol_30m")
    updates["rvol_3d"] = rvol.get("rvol_3d")
    updates["rvol_monthly"] = rvol.get("rvol_monthly")

    # Flow aggression metrics
    flow_agg = await get_flow_aggression(ticker, entry_ts)
    updates["ask_side_ratio"] = flow_agg.get("ask_side_ratio")
    updates["sweep_ratio_1h"] = flow_agg.get("sweep_ratio_1h")
    updates["same_ticker_premium_1h"] = flow_agg.get("same_ticker_premium_1h")

    # Institutional flow
    updates["institutional_flow_1w"] = await get_institutional_flow_1w(ticker, entry_ts)

    # Market tide
    tide_data = await get_market_tide_before_entry(entry_ts, minutes=30)
    updates["market_tide_30m"] = tide_data.get("net_premium")
    updates["market_tide_direction"] = tide_data.get("direction")

    # Regime at entry
    regime_data = await get_regime_at_entry(entry_ts)
    updates["trend_regime_at_entry"] = regime_data.get("trend_regime")
    updates["vol_regime_at_entry"] = regime_data.get("vol_regime")
    updates["risk_regime_at_entry"] = regime_data.get("risk_regime")
    updates["session_regime_at_entry"] = regime_data.get("session_regime")
    updates["vix_at_entry"] = regime_data.get("vix_at_entry")
    updates["vix_regime_at_entry"] = regime_data.get("vix_regime")

    # P2 features: OI change and IV vs HV
    from orion.main_price_target_labeler import get_p2_features, get_p3_features, get_sector_correlation_features

    option_chain = record.get("option_chain", "")
    expiry = record.get("expiry")

    p2 = await get_p2_features(ticker, option_chain, entry_ts)
    updates["oi_change_1d"] = p2.get("oi_change_1d")
    updates["oi_change_pct"] = p2.get("oi_change_pct")
    updates["iv_vs_hv_ratio"] = p2.get("iv_vs_hv_ratio")

    # P3 features: 52w high, spread detection
    if expiry:
        p3 = await get_p3_features(ticker, option_chain, expiry, entry_ts)
        updates["high_52w_distance_pct"] = p3.get("high_52w_distance_pct")
        updates["is_spread_leg"] = p3.get("is_spread_leg")
        updates["same_expiry_trades_1h"] = p3.get("same_expiry_trades_1h")

    # Sector correlation and flow features
    sector = updates.get("sector")
    if sector:
        sector_corr = await get_sector_correlation_features(ticker, entry_ts)
        updates["sector_net_premium_1h"] = sector_corr.get("sector_net_premium_1h")
        updates["sector_flow_direction"] = sector_corr.get("sector_flow_direction")
        updates["spy_correlation_5d"] = sector_corr.get("spy_correlation_5d")
        updates["spy_return_1h"] = sector_corr.get("spy_return_1h")

    # IV rank (from UW)
    from orion.main_price_target_labeler import get_iv_rank_at_entry

    updates["iv_rank_at_entry"] = await get_iv_rank_at_entry(ticker, entry_ts)

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
