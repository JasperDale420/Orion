"""
Option Quote Tracker Service.

Fetches real option prices from Alpaca at checkpoint intervals
for accurate ML training labels.
"""

import asyncio
import logging
import os
import re
import signal
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from orion.connectors.alpaca_option_greeks_connector import AlpacaOptionGreeksConnector
from orion.shared.db_utils import db_query, db_write
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.option_quote_tracker")

# Checkpoint intervals in minutes from entry
CHECKPOINTS = {
    "entry": 0,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "8h": 480,
    "1d": 1440,
}

# Max age for tracking (don't fetch quotes for events older than this)
MAX_TRACKING_AGE_HOURS = 24

# Batch size for API calls
BATCH_SIZE = 100

# Polling interval
POLL_INTERVAL_SECONDS = 60

shutdown_event = asyncio.Event()


def handle_shutdown(signum: int, frame: Any) -> None:
    """Handle shutdown signals."""
    logger.info(f"Received signal {signum}, initiating shutdown...")
    shutdown_event.set()


def extract_underlying_ticker(option_symbol: str) -> str:
    """Extract underlying ticker from OCC option symbol."""
    if not option_symbol:
        return ""
    # Option symbol pattern: <TICKER><YYMMDD><C|P><STRIKE>
    match = re.match(r"^([A-Z]+)\d{6}[CP]\d+$", option_symbol)
    return match.group(1) if match else option_symbol


async def get_pending_checkpoints() -> List[Dict[str, Any]]:
    """Get flow events that need checkpoint quotes fetched."""

    async def query(session: Any) -> List[Dict[str, Any]]:
        # Find flow events from last 24 hours
        # Construct option symbol from components: TICKER + YYMMDD + C/P + strike*1000 padded
        stmt = text(
            """
            SELECT 
                f.event_id,
                f.ticker,
                f.ticker || TO_CHAR(TO_DATE(f.expiry, 'YYYY-MM-DD'), 'YYMMDD') || 
                    f.put_call || LPAD(CAST((f.strike * 1000)::bigint AS text), 8, '0') as option_symbol,
                f.flow_ts_utc,
                EXTRACT(EPOCH FROM (NOW() - f.flow_ts_utc)) / 60 as minutes_since_entry
            FROM silver_uw_flow f
            WHERE f.flow_ts_utc > NOW() - INTERVAL '24 hours'
            AND f.expiry IS NOT NULL
            AND f.strike IS NOT NULL
            ORDER BY f.flow_ts_utc DESC
            LIMIT 1000
        """
        )
        result = await session.execute(stmt)
        return [dict(row._mapping) for row in result.fetchall()]

    return await db_query(query)


async def get_existing_quotes(event_ids: List[str]) -> Dict[str, set]:
    """Get checkpoints already fetched for given events."""
    if not event_ids:
        return {}

    async def query(session: Any) -> Dict[str, set]:
        stmt = text(
            """
            SELECT flow_event_id, checkpoint
            FROM silver_option_quotes
            WHERE flow_event_id = ANY(:event_ids)
        """
        )
        result = await session.execute(stmt, {"event_ids": event_ids})
        existing: Dict[str, set] = {}
        for row in result.fetchall():
            if row[0] not in existing:
                existing[row[0]] = set()
            existing[row[0]].add(row[1])
        return existing

    return await db_query(query)


async def store_quote(
    flow_event_id: str,
    option_symbol: str,
    underlying_ticker: str,
    checkpoint: str,
    ts_utc: datetime,
    quote_data: Dict[str, Any],
) -> None:
    """Store option quote to database."""

    async def write(session: Any) -> None:
        stmt = text(
            """
            INSERT INTO silver_option_quotes (
                flow_event_id, option_symbol, underlying_ticker, checkpoint, ts_utc,
                bid_price, ask_price, mid_price, last_trade_price,
                delta, gamma, theta, vega, iv
            ) VALUES (
                :flow_event_id, :option_symbol, :underlying_ticker, :checkpoint, :ts_utc,
                :bid_price, :ask_price, :mid_price, :last_trade_price,
                :delta, :gamma, :theta, :vega, :iv
            )
            ON CONFLICT (flow_event_id, checkpoint) DO UPDATE SET
                bid_price = EXCLUDED.bid_price,
                ask_price = EXCLUDED.ask_price,
                mid_price = EXCLUDED.mid_price,
                last_trade_price = EXCLUDED.last_trade_price,
                delta = EXCLUDED.delta,
                gamma = EXCLUDED.gamma,
                theta = EXCLUDED.theta,
                vega = EXCLUDED.vega,
                iv = EXCLUDED.iv
        """
        )
        await session.execute(
            stmt,
            {
                "flow_event_id": flow_event_id,
                "option_symbol": option_symbol,
                "underlying_ticker": underlying_ticker,
                "checkpoint": checkpoint,
                "ts_utc": ts_utc,
                "bid_price": quote_data.get("bid_price"),
                "ask_price": quote_data.get("ask_price"),
                "mid_price": quote_data.get("mid_price"),
                "last_trade_price": quote_data.get("last_trade_price"),
                "delta": quote_data.get("delta"),
                "gamma": quote_data.get("gamma"),
                "theta": quote_data.get("theta"),
                "vega": quote_data.get("vega"),
                "iv": quote_data.get("implied_volatility"),
            },
        )

    await db_write(write)


async def run_quote_tracker() -> None:
    """Main quote tracking loop."""
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    logger.info("Option Quote Tracker starting...")

    connector = AlpacaOptionGreeksConnector()

    while not shutdown_event.is_set():
        try:
            now = datetime.now(timezone.utc)

            # Get pending flow events
            flow_events = await get_pending_checkpoints()
            if not flow_events:
                logger.debug("No pending flow events to track")
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            # Get existing quotes
            event_ids = [e["event_id"] for e in flow_events]
            existing = await get_existing_quotes(event_ids)

            # Determine which checkpoints need fetching
            symbols_to_fetch: Dict[str, List[Dict[str, Any]]] = {}  # symbol -> list of (event, checkpoint)

            for event in flow_events:
                event_id = event["event_id"]
                option_symbol = event["option_symbol"]
                minutes_since = float(event["minutes_since_entry"])
                event_existing = existing.get(event_id, set())

                for checkpoint, checkpoint_minutes in CHECKPOINTS.items():
                    # Skip if already fetched
                    if checkpoint in event_existing:
                        continue

                    # Check if checkpoint time has passed
                    if minutes_since >= checkpoint_minutes:
                        if option_symbol not in symbols_to_fetch:
                            symbols_to_fetch[option_symbol] = []
                        symbols_to_fetch[option_symbol].append(
                            {"event": event, "checkpoint": checkpoint, "checkpoint_minutes": checkpoint_minutes}
                        )

            if not symbols_to_fetch:
                logger.debug("All checkpoints up to date")
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            # Batch fetch quotes
            all_symbols = list(symbols_to_fetch.keys())
            total_stored = 0

            for i in range(0, len(all_symbols), BATCH_SIZE):
                batch_symbols = all_symbols[i : i + BATCH_SIZE]
                quotes = await connector.get_greeks_batch(batch_symbols)

                for symbol in batch_symbols:
                    quote_data = quotes.get(symbol, {})
                    if not quote_data.get("mid_price") and not quote_data.get("last_trade_price"):
                        continue  # No price data

                    for item in symbols_to_fetch[symbol]:
                        event = item["event"]
                        checkpoint = item["checkpoint"]
                        checkpoint_minutes = item["checkpoint_minutes"]

                        # Calculate checkpoint timestamp
                        checkpoint_ts = event["flow_ts_utc"] + timedelta(minutes=checkpoint_minutes)

                        await store_quote(
                            flow_event_id=event["event_id"],
                            option_symbol=symbol,
                            underlying_ticker=event["ticker"],
                            checkpoint=checkpoint,
                            ts_utc=checkpoint_ts,
                            quote_data=quote_data,
                        )
                        total_stored += 1

                # Rate limit between batches
                await asyncio.sleep(0.5)

            logger.info(
                f"Stored {total_stored} option quotes for {len(symbols_to_fetch)} symbols",
                extra={"event_type": "QUOTES_STORED", "count": total_stored},
            )

        except Exception as e:
            logger.error(f"Quote tracker error: {e}", exc_info=True)

        # Wait before next cycle
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=POLL_INTERVAL_SECONDS)
            break
        except asyncio.TimeoutError:
            pass

    logger.info("Option Quote Tracker stopped")


def main() -> None:
    """Entry point."""
    asyncio.run(run_quote_tracker())


if __name__ == "__main__":
    main()
