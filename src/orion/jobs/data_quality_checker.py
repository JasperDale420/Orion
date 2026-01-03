"""
Data Quality Checker - Scheduled job to detect data quality issues.

Runs hourly to check for:
1. Zero-valued bars in recent data
2. Missing/stale data for critical tickers
3. Data gaps during market hours
4. UW Flow data quality
5. Darkpool data quality
6. ML Features population status

Usage:
    docker-compose run --rm price_target_labeler python -m orion.jobs.data_quality_checker
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import text

from orion.shared.db_utils import db_query
from orion.storage.db import init_db

logger = logging.getLogger(__name__)

# Critical tickers that must have continuous data
CRITICAL_TICKERS = ["SPY", "QQQ", "IWM", "NVDA", "AAPL", "TSLA"]

# Market hours (Eastern Time, simplified as UTC-5)
MARKET_OPEN_HOUR = 14  # 9:30 ET = 14:30 UTC
MARKET_CLOSE_HOUR = 21  # 4:00 PM ET = 21:00 UTC


# =============================================================================
# ALPACA BARS CHECKS
# =============================================================================

async def check_zero_valued_bars(lookback_hours: int = 24) -> List[Dict]:
    """Check for bars with invalid close values in recent data."""
    async def query(session: Any) -> List[Dict]:
        stmt = text("""
            SELECT ticker, COUNT(*) as zero_count, 
                   MIN(bar_start_ts_utc) as earliest,
                   MAX(bar_start_ts_utc) as latest
            FROM silver_alpaca_bars
            WHERE (close = 0 OR close IS NULL)
              AND bar_start_ts_utc > NOW() - INTERVAL ':hours hours'
            GROUP BY ticker
            ORDER BY zero_count DESC
        """.replace(":hours", str(lookback_hours)))
        result = await session.execute(stmt)
        return [
            {"ticker": r[0], "zero_count": r[1], "earliest": r[2], "latest": r[3]}
            for r in result.fetchall()
        ]
    return await db_query(query)


async def check_data_staleness(stale_minutes: int = 15) -> List[Dict]:
    """Check for tickers with stale data during market hours."""
    now = datetime.now(timezone.utc)
    current_hour = now.hour
    
    # Only check during market hours
    if current_hour < MARKET_OPEN_HOUR or current_hour >= MARKET_CLOSE_HOUR:
        return []
    
    async def query(session: Any) -> List[Dict]:
        stmt = text("""
            SELECT ticker, MAX(bar_start_ts_utc) as last_bar,
                   EXTRACT(EPOCH FROM (NOW() - MAX(bar_start_ts_utc))) / 60 as minutes_ago
            FROM silver_alpaca_bars
            WHERE ticker = ANY(:tickers)
            GROUP BY ticker
            HAVING EXTRACT(EPOCH FROM (NOW() - MAX(bar_start_ts_utc))) / 60 > :stale_minutes
        """)
        result = await session.execute(stmt, {
            "tickers": CRITICAL_TICKERS,
            "stale_minutes": stale_minutes
        })
        return [
            {"ticker": r[0], "last_bar": r[1], "minutes_ago": r[2]}
            for r in result.fetchall()
        ]
    return await db_query(query)


async def check_bar_gaps(ticker: str = "SPY", gap_minutes: int = 5) -> List[Dict]:
    """Check for gaps in bar data for a ticker."""
    async def query(session: Any) -> List[Dict]:
        stmt = text("""
            WITH bar_gaps AS (
                SELECT 
                    bar_start_ts_utc,
                    LAG(bar_start_ts_utc) OVER (ORDER BY bar_start_ts_utc) as prev_bar,
                    EXTRACT(EPOCH FROM (bar_start_ts_utc - LAG(bar_start_ts_utc) OVER (ORDER BY bar_start_ts_utc))) / 60 as gap_minutes
                FROM silver_alpaca_bars
                WHERE ticker = :ticker
                  AND bar_start_ts_utc > NOW() - INTERVAL '24 hours'
                  AND EXTRACT(HOUR FROM bar_start_ts_utc) >= :market_open
                  AND EXTRACT(HOUR FROM bar_start_ts_utc) < :market_close
            )
            SELECT bar_start_ts_utc, prev_bar, gap_minutes
            FROM bar_gaps
            WHERE gap_minutes > :gap_minutes
            ORDER BY bar_start_ts_utc DESC
            LIMIT 10
        """)
        result = await session.execute(stmt, {
            "ticker": ticker,
            "gap_minutes": gap_minutes,
            "market_open": MARKET_OPEN_HOUR,
            "market_close": MARKET_CLOSE_HOUR
        })
        return [
            {"bar_ts": r[0], "prev_bar": r[1], "gap_minutes": r[2]}
            for r in result.fetchall()
        ]
    return await db_query(query)


async def get_bars_summary() -> Dict:
    """Get Alpaca bars data quality summary."""
    async def query(session: Any) -> Dict:
        stmt = text("""
            SELECT 
                COUNT(*) as total_bars,
                SUM(CASE WHEN close > 0 THEN 1 ELSE 0 END) as valid_bars,
                SUM(CASE WHEN close = 0 OR close IS NULL THEN 1 ELSE 0 END) as invalid_bars,
                COUNT(DISTINCT ticker) as unique_tickers,
                MAX(bar_start_ts_utc) as latest_bar
            FROM silver_alpaca_bars
            WHERE bar_start_ts_utc > NOW() - INTERVAL '24 hours'
        """)
        result = await session.execute(stmt)
        row = result.fetchone()
        return {
            "total_bars_24h": row[0] or 0,
            "valid_bars": row[1] or 0,
            "invalid_bars": row[2] or 0,
            "unique_tickers": row[3] or 0,
            "latest_bar": row[4],
            "validity_pct": round(100 * (row[1] or 0) / row[0], 2) if row[0] and row[0] > 0 else 0
        }
    return await db_query(query)


# =============================================================================
# UW FLOW CHECKS
# =============================================================================

async def get_flow_summary() -> Dict:
    """Get UW Flow data quality summary."""
    async def query(session: Any) -> Dict:
        stmt = text("""
            SELECT 
                COUNT(*) as total_flows,
                COUNT(CASE WHEN premium_usd IS NOT NULL AND premium_usd > 0 THEN 1 END) as valid_premium,
                COUNT(CASE WHEN premium_usd IS NULL OR premium_usd = 0 THEN 1 END) as missing_premium,
                COUNT(DISTINCT ticker) as unique_tickers,
                MAX(flow_ts_utc) as latest_flow
            FROM silver_uw_flow
            WHERE flow_ts_utc > NOW() - INTERVAL '24 hours'
        """)
        result = await session.execute(stmt)
        row = result.fetchone()
        total = row[0] or 0
        valid = row[1] or 0
        return {
            "total_flows_24h": total,
            "valid_premium": valid,
            "missing_premium": row[2] or 0,
            "unique_tickers": row[3] or 0,
            "latest_flow": row[4],
            "validity_pct": round(100 * valid / total, 2) if total > 0 else 0
        }
    return await db_query(query)


async def check_flow_staleness(stale_minutes: int = 30) -> bool:
    """Check if flow data is stale during market hours."""
    now = datetime.now(timezone.utc)
    current_hour = now.hour
    
    if current_hour < MARKET_OPEN_HOUR or current_hour >= MARKET_CLOSE_HOUR:
        return False  # Outside market hours, no alert
    
    async def query(session: Any) -> bool:
        stmt = text("""
            SELECT EXTRACT(EPOCH FROM (NOW() - MAX(flow_ts_utc))) / 60 as minutes_ago
            FROM silver_uw_flow
        """)
        result = await session.execute(stmt)
        row = result.fetchone()
        return row[0] is not None and row[0] > stale_minutes
    return await db_query(query)


# =============================================================================
# DARKPOOL CHECKS
# =============================================================================

async def get_darkpool_summary() -> Dict:
    """Get Darkpool data quality summary."""
    async def query(session: Any) -> Dict:
        stmt = text("""
            SELECT 
                COUNT(*) as total_trades,
                COUNT(CASE WHEN size_shares IS NOT NULL AND size_shares > 0 THEN 1 END) as valid_trades,
                COUNT(CASE WHEN trade_price IS NULL OR trade_price = 0 THEN 1 END) as invalid_price,
                COUNT(DISTINCT ticker) as unique_tickers,
                MAX(dark_ts_utc) as latest_trade
            FROM silver_uw_darkpool
            WHERE dark_ts_utc > NOW() - INTERVAL '24 hours'
        """)
        result = await session.execute(stmt)
        row = result.fetchone()
        total = row[0] or 0
        valid = row[1] or 0
        return {
            "total_trades_24h": total,
            "valid_trades": valid,
            "invalid_price": row[2] or 0,
            "unique_tickers": row[3] or 0,
            "latest_trade": row[4],
            "validity_pct": round(100 * valid / total, 2) if total > 0 else 0
        }
    return await db_query(query)


async def check_darkpool_staleness(stale_minutes: int = 60) -> bool:
    """Check if darkpool data is stale during market hours."""
    now = datetime.now(timezone.utc)
    current_hour = now.hour
    
    if current_hour < MARKET_OPEN_HOUR or current_hour >= MARKET_CLOSE_HOUR:
        return False
    
    async def query(session: Any) -> bool:
        stmt = text("""
            SELECT EXTRACT(EPOCH FROM (NOW() - MAX(dark_ts_utc))) / 60 as minutes_ago
            FROM silver_uw_darkpool
        """)
        result = await session.execute(stmt)
        row = result.fetchone()
        return row[0] is not None and row[0] > stale_minutes
    return await db_query(query)


# =============================================================================
# ML FEATURES (PRICE TARGET LABELS) CHECKS
# =============================================================================

async def get_ml_features_summary() -> Dict:
    """Get ML features population summary for price_target_labels."""
    async def query(session: Any) -> Dict:
        stmt = text("""
            SELECT 
                COUNT(*) as total_labels,
                COUNT(rvol_1h) as has_rvol,
                COUNT(spy_return_1h) as has_spy_return,
                COUNT(high_52w_distance_pct) as has_52w,
                COUNT(darkpool_volume_1h) as has_darkpool,
                COUNT(minutes_to_close) as has_minutes,
                COUNT(overnight_gap_pct) as has_gap,
                COUNT(vwap_distance_pct) as has_vwap,
                MAX(entry_ts) as latest_entry
            FROM price_target_labels
        """)
        result = await session.execute(stmt)
        row = result.fetchone()
        total = row[0] or 1  # Avoid div by zero
        return {
            "total_labels": row[0] or 0,
            "rvol_1h_pct": round(100 * (row[1] or 0) / total, 1),
            "spy_return_pct": round(100 * (row[2] or 0) / total, 1),
            "high_52w_pct": round(100 * (row[3] or 0) / total, 1),
            "darkpool_pct": round(100 * (row[4] or 0) / total, 1),
            "minutes_pct": round(100 * (row[5] or 0) / total, 1),
            "gap_pct": round(100 * (row[6] or 0) / total, 1),
            "vwap_pct": round(100 * (row[7] or 0) / total, 1),
            "latest_entry": row[8]
        }
    return await db_query(query)


async def check_recent_labels_features() -> Dict:
    """Check ML feature population for labels created in last 24 hours."""
    async def query(session: Any) -> Dict:
        stmt = text("""
            SELECT 
                COUNT(*) as total,
                COUNT(rvol_1h) as has_rvol,
                COUNT(spy_return_1h) as has_spy,
                COUNT(darkpool_volume_1h) as has_dp,
                COUNT(minutes_to_close) as has_min
            FROM price_target_labels
            WHERE entry_ts > NOW() - INTERVAL '24 hours'
        """)
        result = await session.execute(stmt)
        row = result.fetchone()
        total = row[0] or 1
        return {
            "recent_labels": row[0] or 0,
            "rvol_pct": round(100 * (row[1] or 0) / total, 1),
            "spy_pct": round(100 * (row[2] or 0) / total, 1),
            "darkpool_pct": round(100 * (row[3] or 0) / total, 1),
            "minutes_pct": round(100 * (row[4] or 0) / total, 1)
        }
    return await db_query(query)


# =============================================================================
# MAIN RUNNER
# =============================================================================

async def run_quality_checks():
    """Run all data quality checks and log results."""
    await init_db()
    
    logger.info("=" * 60)
    logger.info("STARTING DATA QUALITY CHECKS")
    logger.info("=" * 60)
    
    results = {}
    
    # 1. Alpaca Bars Summary
    bars_summary = await get_bars_summary()
    results["bars"] = bars_summary
    logger.info(f"[BARS] 24h: {bars_summary['total_bars_24h']} bars, "
                f"{bars_summary['validity_pct']}% valid, "
                f"{bars_summary['unique_tickers']} tickers")
    
    # 2. Zero-valued bars
    zero_bars = await check_zero_valued_bars(lookback_hours=1)
    results["zero_bars"] = zero_bars
    if zero_bars:
        logger.warning(f"[BARS] ALERT: {len(zero_bars)} tickers with zero-valued bars!")
    else:
        logger.info("[BARS] No zero-valued bars in last hour ✓")
    
    # 3. Bar Staleness
    stale = await check_data_staleness(stale_minutes=15)
    results["stale_bars"] = stale
    if stale:
        logger.warning(f"[BARS] ALERT: {len(stale)} critical tickers stale!")
    else:
        logger.info("[BARS] All critical tickers fresh ✓")
    
    # 4. SPY Gaps
    gaps = await check_bar_gaps("SPY", gap_minutes=5)
    results["spy_gaps"] = gaps
    if gaps:
        logger.warning(f"[BARS] ALERT: {len(gaps)} gaps in SPY bars")
    else:
        logger.info("[BARS] No SPY gaps ✓")
    
    # 5. UW Flow Summary
    flow_summary = await get_flow_summary()
    results["flow"] = flow_summary
    logger.info(f"[FLOW] 24h: {flow_summary['total_flows_24h']} flows, "
                f"{flow_summary['validity_pct']}% valid premium, "
                f"{flow_summary['unique_tickers']} tickers")
    
    flow_stale = await check_flow_staleness(stale_minutes=30)
    if flow_stale:
        logger.warning("[FLOW] ALERT: Flow data is stale (>30 min)")
    else:
        logger.info("[FLOW] Flow data fresh ✓")
    
    # 6. Darkpool Summary
    dp_summary = await get_darkpool_summary()
    results["darkpool"] = dp_summary
    logger.info(f"[DARKPOOL] 24h: {dp_summary['total_trades_24h']} trades, "
                f"{dp_summary['validity_pct']}% valid, "
                f"{dp_summary['unique_tickers']} tickers")
    
    dp_stale = await check_darkpool_staleness(stale_minutes=60)
    if dp_stale:
        logger.warning("[DARKPOOL] ALERT: Darkpool data is stale (>60 min)")
    else:
        logger.info("[DARKPOOL] Darkpool data fresh ✓")
    
    # 7. ML Features Summary
    ml_summary = await get_ml_features_summary()
    results["ml_features"] = ml_summary
    logger.info(f"[ML] Total labels: {ml_summary['total_labels']}")
    logger.info(f"[ML] Feature coverage: rvol={ml_summary['rvol_1h_pct']}%, "
                f"spy={ml_summary['spy_return_pct']}%, "
                f"darkpool={ml_summary['darkpool_pct']}%")
    
    # 8. Recent Labels Features
    recent = await check_recent_labels_features()
    results["recent_labels"] = recent
    if recent["recent_labels"] > 0:
        logger.info(f"[ML] Recent 24h: {recent['recent_labels']} labels, "
                    f"rvol={recent['rvol_pct']}%, spy={recent['spy_pct']}%")
    
    logger.info("=" * 60)
    logger.info("DATA QUALITY CHECKS COMPLETE")
    logger.info("=" * 60)
    
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    asyncio.run(run_quality_checks())

