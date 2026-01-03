"""
Continuous Flow Labeling Service.

Runs alongside ingestion to label UW flow records with price outcomes.
Labels each flow with actual returns at 15m, 30m, 1h, 2h horizons.
"""

import asyncio
import signal
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from orion.shared.db_utils import db_query, db_write
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db

logger = setup_struct_logger("orion.labeler")

# Configuration
BATCH_SIZE = 100
POLL_INTERVAL_SECONDS = 60
MIN_AGE_MINUTES = 130  # Only label flows older than 2h10m (so 2h price is available)


async def get_unlabeled_flows(limit: int = BATCH_SIZE) -> List[Any]:
    """Get flow records that haven't been labeled yet."""

    async def query(session: Any) -> List[Any]:
        # Get flows older than MIN_AGE_MINUTES that aren't in flow_labels yet
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=MIN_AGE_MINUTES)

        stmt = text(
            """
            SELECT f.*
            FROM silver_uw_flow f
            LEFT JOIN flow_labels l ON f.event_id = l.event_id
            WHERE l.event_id IS NULL
            AND f.flow_ts_utc < :cutoff
            ORDER BY f.flow_ts_utc ASC
            LIMIT :limit
        """
        )

        result = await session.execute(stmt, {"cutoff": cutoff, "limit": limit})
        return result.fetchall()

    return await db_query(query)


async def get_price_at_time(ticker: str, target_ts: datetime, tolerance_minutes: int = 10) -> Optional[float]:
    """Get underlying price at a specific time from flow data (bars have zero prices)."""

    async def query(session: Any) -> Optional[float]:
        window_start = target_ts - timedelta(minutes=tolerance_minutes)
        window_end = target_ts + timedelta(minutes=tolerance_minutes)

        # Use flow records' underlying_price since bar data is broken
        stmt = text(
            """
            SELECT underlying_price
            FROM silver_uw_flow
            WHERE ticker = :ticker
            AND flow_ts_utc >= :window_start
            AND flow_ts_utc <= :window_end
            AND underlying_price > 0
            ORDER BY ABS(EXTRACT(EPOCH FROM flow_ts_utc - :target_ts))
            LIMIT 1
        """
        )

        result = await session.execute(
            stmt,
            {
                "ticker": ticker,
                "window_start": window_start,
                "window_end": window_end,
                "target_ts": target_ts,
            },
        )
        row = result.fetchone()
        return float(row[0]) if row else None

    return await db_query(query)


def calculate_return(entry_price: float, exit_price: float) -> Optional[float]:
    """Calculate percentage return."""
    if entry_price <= 0 or exit_price is None:
        return None
    return ((exit_price - entry_price) / entry_price) * 100


def classify_return(return_pct: Optional[float], threshold: float = 0.1) -> str:
    """Classify return as WIN/LOSS/FLAT."""
    if return_pct is None:
        return "UNKNOWN"
    if return_pct > threshold:
        return "WIN"
    elif return_pct < -threshold:
        return "LOSS"
    return "FLAT"


def parse_expiry(expiry_str: Optional[str]) -> Optional[datetime]:
    """Parse expiry string to datetime."""
    if not expiry_str:
        return None
    try:
        return datetime.strptime(expiry_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def calculate_dte(flow_ts: datetime, expiry: Optional[datetime]) -> Optional[int]:
    """Calculate days to expiry."""
    if not expiry:
        return None
    dte = (expiry.date() - flow_ts.date()).days
    return max(0, dte)


def classify_trade_type(dte: Optional[int]) -> str:
    """Classify trade type based on DTE."""
    if dte is None:
        return "UNKNOWN"
    if dte == 0:
        return "0DTE"
    elif dte <= 3:
        return "SHORT_SWING"
    elif dte <= 14:
        return "SWING"
    else:
        return "POSITION"


async def label_flow(flow: Any) -> Optional[Dict[str, Any]]:
    """Label a single flow record with price outcomes and DTE classification."""
    ticker = flow.ticker
    flow_ts = flow.flow_ts_utc
    entry_price = flow.underlying_price or 0

    if entry_price <= 0:
        return None

    # Parse expiry and calculate DTE
    expiry = parse_expiry(flow.expiry)
    dte = calculate_dte(flow_ts, expiry)
    trade_type = classify_trade_type(dte)

    # Get prices at different horizons
    price_15m = await get_price_at_time(ticker, flow_ts + timedelta(minutes=15))
    price_30m = await get_price_at_time(ticker, flow_ts + timedelta(minutes=30))
    price_1h = await get_price_at_time(ticker, flow_ts + timedelta(hours=1))
    price_2h = await get_price_at_time(ticker, flow_ts + timedelta(hours=2))

    # Calculate returns
    return_15m = calculate_return(entry_price, price_15m)
    return_30m = calculate_return(entry_price, price_30m)
    return_1h = calculate_return(entry_price, price_1h)
    return_2h = calculate_return(entry_price, price_2h)

    # Select primary return based on trade type
    # 0DTE: Use 30m (intraday scalp)
    # SHORT_SWING: Use 1h (quick swing)
    # SWING: Use 2h (multi-day but measure intraday)
    # POSITION: Use 2h (longer hold, but limited by our horizons)
    if trade_type == "0DTE":
        primary_return = return_30m
        primary_horizon = "30m"
    elif trade_type == "SHORT_SWING":
        primary_return = return_1h
        primary_horizon = "1h"
    else:  # SWING or POSITION
        primary_return = return_2h
        primary_horizon = "2h"

    primary_label = classify_return(primary_return)

    return {
        "event_id": flow.event_id,
        "ticker": ticker,
        "flow_ts_utc": flow_ts,
        "expiry": expiry,
        "dte": dte,
        "trade_type": trade_type,
        "underlying_price_entry": entry_price,
        "option_price_entry": flow.option_price,
        "premium_usd": flow.premium_usd,
        "aggressor": flow.aggressor,
        "put_call": flow.put_call,
        "is_sweep": flow.is_sweep == "true" if isinstance(flow.is_sweep, str) else flow.is_sweep,
        "iv": flow.iv,
        "price_15m": price_15m,
        "price_30m": price_30m,
        "price_1h": price_1h,
        "price_2h": price_2h,
        "return_15m": return_15m,
        "return_30m": return_30m,
        "return_1h": return_1h,
        "return_2h": return_2h,
        "label_15m": classify_return(return_15m),
        "label_30m": classify_return(return_30m),
        "label_1h": classify_return(return_1h),
        "label_2h": classify_return(return_2h),
        "primary_return": primary_return,
        "primary_label": primary_label,
        "primary_horizon": primary_horizon,
    }


async def persist_labels(labels: List[Dict[str, Any]]) -> int:
    """Persist labeled records to database."""
    if not labels:
        return 0

    async def write(session: Any) -> None:
        stmt = text(
            """
            INSERT INTO flow_labels (
                event_id, ticker, flow_ts_utc,
                expiry, dte, trade_type,
                underlying_price_entry, option_price_entry, premium_usd,
                aggressor, put_call, is_sweep, iv,
                price_15m, price_30m, price_1h, price_2h,
                return_15m, return_30m, return_1h, return_2h,
                label_15m, label_30m, label_1h, label_2h,
                primary_return, primary_label, primary_horizon
            ) VALUES (
                :event_id, :ticker, :flow_ts_utc,
                :expiry, :dte, :trade_type,
                :underlying_price_entry, :option_price_entry, :premium_usd,
                :aggressor, :put_call, :is_sweep, :iv,
                :price_15m, :price_30m, :price_1h, :price_2h,
                :return_15m, :return_30m, :return_1h, :return_2h,
                :label_15m, :label_30m, :label_1h, :label_2h,
                :primary_return, :primary_label, :primary_horizon
            )
            ON CONFLICT (event_id) DO NOTHING
        """
        )

        for label in labels:
            await session.execute(stmt, label)

    await db_write(write)
    return len(labels)


async def run_labeling_loop(shutdown_event: asyncio.Event) -> None:
    """Main labeling loop."""
    await init_db()

    logger.info("Starting Flow Labeling Service...")

    total_labeled = 0

    while not shutdown_event.is_set():
        try:
            # Get unlabeled flows
            flows = await get_unlabeled_flows(BATCH_SIZE)

            if flows:
                # Label each flow
                labels = []
                for flow in flows:
                    label = await label_flow(flow)
                    if label:
                        labels.append(label)

                # Persist
                if labels:
                    count = await persist_labels(labels)
                    total_labeled += count

                    # Log returns distribution
                    wins = sum(1 for label in labels if label["label_1h"] == "WIN")
                    losses = sum(1 for label in labels if label["label_1h"] == "LOSS")
                    avg_return = sum(label["return_1h"] or 0 for label in labels) / len(labels) if labels else 0

                    logger.info(
                        f"Labeled {count} flows | Total: {total_labeled} | W/L: {wins}/{losses} | Avg 1h Return: {avg_return:+.2f}%",
                        extra={
                            "event_type": "BATCH_LABELED",
                            "batch_size": count,
                            "total_labeled": total_labeled,
                            "wins": wins,
                            "losses": losses,
                            "avg_return_1h": avg_return,
                        },
                    )
            else:
                logger.debug("No unlabeled flows found, waiting...")

        except Exception as e:
            logger.error(f"Labeling error: {e}")
            await asyncio.sleep(5)
            continue

        # Wait for next poll
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=POLL_INTERVAL_SECONDS)
            break
        except asyncio.TimeoutError:
            pass

    logger.info(f"Labeling Service stopped. Total labeled: {total_labeled}")


async def main() -> None:
    """Main entry point."""
    shutdown_event = asyncio.Event()
    loop = asyncio.get_event_loop()

    def handle_signal(sig: int) -> None:
        logger.info(f"Received signal {sig}. Shutting down...")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: handle_signal(s))

    await run_labeling_loop(shutdown_event)


if __name__ == "__main__":
    asyncio.run(main())
