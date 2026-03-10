"""
Darkpool Feature Aggregator.

Aggregates darkpool data into ML features for scoring.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from orion.clients.heber_reader import get_heber_reader
from orion.shared.dataframe_utils import first_existing_column as _first_existing_column
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.ml.darkpool_features")


def _zero_darkpool_features() -> dict[str, Any]:
    return {
        "darkpool_volume_24h": 0.0,
        "darkpool_trade_count": 0,
        "darkpool_avg_price": 0.0,
        "darkpool_max_block": 0.0,
        "darkpool_dollar_volume": 0.0,
    }


def _normalize_ticker(value: Any) -> str | None:
    if value is None:
        return None
    ticker = str(value).strip().upper()
    if not ticker:
        return None
    if ":" in ticker:
        ticker = ticker.split(":")[-1]
    return ticker or None


async def get_darkpool_features(
    ticker: str,
    as_of: datetime,
    lookback_hours: int = 24,
) -> dict[str, Any]:
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
        as_of = as_of.replace(tzinfo=UTC)

    cutoff = as_of - timedelta(hours=lookback_hours)
    try:
        reader = get_heber_reader()
        frame = await asyncio.to_thread(
            reader.read_darkpool,
            symbols=[ticker],
            asof_time=as_of,
            start_time=cutoff,
        )

        if frame.empty:
            return _zero_darkpool_features()

        ticker_col = _first_existing_column(frame, ("ticker", "symbol", "instrument_key"))
        ts_col = _first_existing_column(frame, ("dark_ts_utc", "ts_event", "timestamp"))
        price_col = _first_existing_column(frame, ("trade_price", "price", "last_price"))
        size_col = _first_existing_column(frame, ("size_shares", "size", "shares"))
        if not (ticker_col and ts_col and price_col and size_col):
            return _zero_darkpool_features()

        rows = frame[[ticker_col, ts_col, price_col, size_col]].copy()
        rows["ticker_norm"] = rows[ticker_col].map(_normalize_ticker)
        rows["ts"] = pd.to_datetime(rows[ts_col], utc=True, errors="coerce")
        rows["price"] = pd.to_numeric(rows[price_col], errors="coerce")
        rows["size"] = pd.to_numeric(rows[size_col], errors="coerce")

        ticker_norm = ticker.strip().upper()
        rows = rows[(rows["ticker_norm"] == ticker_norm) & (rows["ts"] >= cutoff) & (rows["ts"] < as_of)]
        rows = rows[rows["price"].notna() & rows["size"].notna() & (rows["size"] > 0)]
        if rows.empty:
            return _zero_darkpool_features()

        total_volume = float(rows["size"].sum())
        trade_count = int(rows.shape[0])
        dollar_volume = float((rows["size"] * rows["price"]).sum())
        max_block = float(rows["size"].max())
        avg_price = float(dollar_volume / total_volume) if total_volume > 0 else 0.0

        return {
            "darkpool_volume_24h": total_volume,
            "darkpool_trade_count": trade_count,
            "darkpool_avg_price": avg_price,
            "darkpool_max_block": max_block,
            "darkpool_dollar_volume": dollar_volume,
        }
    except Exception as e:
        logger.warning(f"Failed to fetch darkpool features for {ticker}: {e}")
        return _zero_darkpool_features()


def get_darkpool_score_boost(features: dict[str, Any], underlying_price: float) -> float:
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
