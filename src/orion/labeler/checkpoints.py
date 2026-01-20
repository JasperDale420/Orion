"""
Price checkpoint utilities for the labeler.

Functions for extracting prices at specific time offsets from entry.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


def get_price_at_offset(
    prices: List[Dict[str, Any]], entry_ts: datetime, hours: int
) -> Optional[float]:
    """Get price at a specific hours offset from entry.

    Args:
        prices: List of price records with 'flow_ts' and 'premium' keys
        entry_ts: Entry timestamp
        hours: Hours offset from entry

    Returns:
        Price at offset or None if not found
    """
    if not prices:
        return None

    target_ts = entry_ts + timedelta(hours=hours)

    # Find closest price within 10-minute window
    best_price = None
    best_diff = timedelta.max

    for p in prices:
        flow_ts = p.get("flow_ts") or p.get("timestamp")
        if not flow_ts:
            continue

        diff = abs(flow_ts - target_ts)
        if diff < best_diff and diff <= timedelta(minutes=10):
            best_diff = diff
            best_price = p.get("premium") or p.get("price")

    return best_price


def get_price_at_offset_minutes(
    prices: List[Dict[str, Any]], entry_ts: datetime, minutes: int
) -> Optional[float]:
    """Get price at a specific minutes offset from entry (for 0DTE).

    Args:
        prices: List of price records with 'flow_ts' and 'premium' keys
        entry_ts: Entry timestamp
        minutes: Minutes offset from entry

    Returns:
        Price at offset or None if not found
    """
    if not prices:
        return None

    target_ts = entry_ts + timedelta(minutes=minutes)

    # Find closest price within 2-minute window for minute-level precision
    best_price = None
    best_diff = timedelta.max

    for p in prices:
        flow_ts = p.get("flow_ts") or p.get("timestamp")
        if not flow_ts:
            continue

        diff = abs(flow_ts - target_ts)
        if diff < best_diff and diff <= timedelta(minutes=2):
            best_diff = diff
            best_price = p.get("premium") or p.get("price")

    return best_price


def get_price_at_offset_days(
    prices: List[Dict[str, Any]], entry_ts: datetime, days: int
) -> Optional[float]:
    """Get price at a specific days offset from entry (for SWING/POSITION).

    Args:
        prices: List of price records with 'flow_ts' and 'premium' keys
        entry_ts: Entry timestamp
        days: Days offset from entry

    Returns:
        Price at offset or None if not found
    """
    if not prices:
        return None

    target_ts = entry_ts + timedelta(days=days)

    # Find closest price within 30-minute window for day-level precision
    best_price = None
    best_diff = timedelta.max

    for p in prices:
        flow_ts = p.get("flow_ts") or p.get("timestamp")
        if not flow_ts:
            continue

        diff = abs(flow_ts - target_ts)
        if diff < best_diff and diff <= timedelta(minutes=30):
            best_diff = diff
            best_price = p.get("premium") or p.get("price")

    return best_price


def calculate_volatility(prices: List[float]) -> Optional[float]:
    """Calculate price volatility (std dev of returns).

    Args:
        prices: List of prices

    Returns:
        Standard deviation of returns or None if insufficient data
    """
    if not prices or len(prices) < 3:
        return None

    returns = []
    for i in range(1, len(prices)):
        if prices[i - 1] > 0:
            returns.append((prices[i] - prices[i - 1]) / prices[i - 1])

    if not returns:
        return None

    import statistics

    return statistics.stdev(returns)


def calculate_return(entry_price: float, current_price: Optional[float]) -> Optional[float]:
    """Calculate return percentage from entry to current price.

    Args:
        entry_price: Entry price
        current_price: Current price

    Returns:
        Return percentage or None if calculation not possible
    """
    if not current_price or not entry_price or entry_price <= 0:
        return None
    return (current_price - entry_price) / entry_price
