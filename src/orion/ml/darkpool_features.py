"""
Darkpool Feature Aggregator.

Aggregates darkpool data into ML features for scoring.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from sqlalchemy import and_, func, select

from orion.shared.db_utils import db_query
from orion.shared.logger import setup_struct_logger
from orion.storage.models_silver import SilverDarkPool

logger = setup_struct_logger("orion.ml.darkpool_features")


async def get_darkpool_features(
    ticker: str,
    as_of: datetime,
    lookback_hours: int = 24,
) -> Dict[str, Any]:
    """
    Aggregate darkpool features for a ticker as of a given timestamp.

    Features:
    - darkpool_volume_24h: Total share volume in darkpool
    - darkpool_trade_count: Number of darkpool prints
    - darkpool_avg_price: Volume-weighted average price
    - darkpool_max_block: Largest single block

    Args:
        ticker: Stock ticker
        as_of: Timestamp to look back from
        lookback_hours: How far back to aggregate (default 24h)

    Returns:
        Dict of darkpool features
    """
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)

    cutoff = as_of - timedelta(hours=lookback_hours)

    async def _query(session: Any) -> Dict[str, Any]:
        # Aggregate query
        stmt = select(
            func.sum(SilverDarkPool.size_shares).label("total_volume"),
            func.count(SilverDarkPool.event_id).label("trade_count"),
            func.avg(SilverDarkPool.trade_price).label("avg_price"),
            func.max(SilverDarkPool.size_shares).label("max_block"),
            func.sum(SilverDarkPool.size_shares * SilverDarkPool.trade_price).label("dollar_volume"),
        ).where(
            and_(
                SilverDarkPool.ticker == ticker,
                SilverDarkPool.dark_ts_utc >= cutoff,
                SilverDarkPool.dark_ts_utc < as_of,
            )
        )

        result = await session.execute(stmt)
        row = result.first()

        if row and row.total_volume:
            # Calculate VWAP
            vwap = row.dollar_volume / row.total_volume if row.total_volume > 0 else None

            return {
                "darkpool_volume_24h": float(row.total_volume or 0),
                "darkpool_trade_count": int(row.trade_count or 0),
                "darkpool_avg_price": float(vwap or row.avg_price or 0),
                "darkpool_max_block": float(row.max_block or 0),
                "darkpool_dollar_volume": float(row.dollar_volume or 0),
            }

        return {
            "darkpool_volume_24h": 0.0,
            "darkpool_trade_count": 0,
            "darkpool_avg_price": 0.0,
            "darkpool_max_block": 0.0,
            "darkpool_dollar_volume": 0.0,
        }

    try:
        return await db_query(_query)
    except Exception as e:
        logger.warning(f"Failed to fetch darkpool features for {ticker}: {e}")
        return {
            "darkpool_volume_24h": 0.0,
            "darkpool_trade_count": 0,
            "darkpool_avg_price": 0.0,
            "darkpool_max_block": 0.0,
            "darkpool_dollar_volume": 0.0,
        }


def get_darkpool_score_boost(features: Dict[str, Any], underlying_price: float) -> float:
    """
    Calculate a score boost based on darkpool activity.

    High darkpool activity aligned with the trade direction = bullish signal.

    Returns:
        Float between 0.0 and 0.15 to add to ML score
    """
    boost = 0.0

    dollar_volume = features.get("darkpool_dollar_volume", 0)
    trade_count = features.get("darkpool_trade_count", 0)
    max_block = features.get("darkpool_max_block", 0)

    # Large dollar volume boost
    if dollar_volume >= 50_000_000:  # $50M+
        boost += 0.08
    elif dollar_volume >= 10_000_000:  # $10M+
        boost += 0.05
    elif dollar_volume >= 1_000_000:  # $1M+
        boost += 0.02

    # High activity boost
    if trade_count >= 50:
        boost += 0.04
    elif trade_count >= 20:
        boost += 0.02

    # Whale block boost
    if underlying_price > 0:
        block_value = max_block * underlying_price
        if block_value >= 5_000_000:  # $5M+ single block
            boost += 0.03

    return min(boost, 0.15)  # Cap at 15% boost
