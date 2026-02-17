"""
Option Quote Tracker Service.

Fetches real option prices from Alpaca at checkpoint intervals
for accurate ML training labels.
"""

import asyncio
import os
import re
import signal
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pandas as pd

from orion.clients.heber_reader import get_heber_reader
from orion.config import SystemSettings
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
_PREFER_HEBER_FALSE_VALUES = {"0", "false", "no", "off", "n"}

shutdown_event = asyncio.Event()
_quote_checkpoint_cache: Dict[str, set[str]] = {}


def _legacy_label_pipeline_control() -> tuple[bool, str, str]:
    settings = SystemSettings()

    specific_key = "ORION_ENABLE_LEGACY_OPTION_QUOTE_TRACKER"
    if settings.legacy_option_quote_tracker_enabled is not None:
        enabled = settings.legacy_option_quote_tracker_enabled
        raw = "true" if enabled else "false"
        return enabled, specific_key, raw

    global_key = "ORION_ENABLE_LEGACY_LABEL_PIPELINES"
    enabled = settings.legacy_label_pipelines_enabled
    raw = "true" if enabled else "false"
    return enabled, global_key, raw


def _legacy_label_pipelines_enabled() -> bool:
    enabled, _, _ = _legacy_label_pipeline_control()
    return enabled


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
    if not _prefer_heber_flow_source():
        return []

    heber_pending = await _get_pending_checkpoints_from_heber()
    return heber_pending or []


def _prefer_heber_flow_source() -> bool:
    raw = os.getenv("ORION_OPTION_QUOTE_TRACKER_PREFER_HEBER", "1").strip().lower()
    return raw not in _PREFER_HEBER_FALSE_VALUES


def _first_existing_column(df: pd.DataFrame, names: List[str]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None


def _coerce_ticker_column(df: pd.DataFrame) -> pd.DataFrame:
    if "ticker" in df.columns:
        return df
    if "symbol" in df.columns:
        return df.assign(ticker=df["symbol"].astype(str).str.upper())
    if "underlying" in df.columns:
        return df.assign(ticker=df["underlying"].astype(str).str.upper())
    if "instrument_key" in df.columns:
        return df.assign(ticker=df["instrument_key"].astype(str).str.split(":").str[-1].str.upper())
    return df


def _normalize_put_call(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().upper()
    if normalized in {"C", "CALL", "CALLS"}:
        return "C"
    if normalized in {"P", "PUT", "PUTS"}:
        return "P"
    return None


def _coerce_expiry(expiry: Any) -> datetime | None:
    if expiry is None:
        return None
    parsed = pd.to_datetime(expiry, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    if isinstance(parsed, pd.Timestamp):
        return parsed.to_pydatetime()
    return None


def _coerce_flow_ts(value: Any) -> datetime | None:
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    if isinstance(parsed, pd.Timestamp):
        return parsed.to_pydatetime()
    return None


def _build_option_symbol(ticker: str, expiry: datetime, put_call: str, strike: float) -> str:
    strike_thousandths = int(float(strike) * 1000)
    return f"{ticker}{expiry.strftime('%y%m%d')}{put_call}{strike_thousandths:08d}"


async def _get_pending_checkpoints_from_heber(limit: int = 1000) -> List[Dict[str, Any]] | None:
    now_utc = datetime.now(timezone.utc)
    start = now_utc - timedelta(hours=24)
    reader = get_heber_reader()
    try:
        flow_df = await asyncio.to_thread(reader.read_flow, asof_time=now_utc, start_time=start)
    except Exception as exc:
        logger.warning(
            "option_quote_tracker_heber_read_failed",
            extra={"event_type": "OPTION_QUOTE_TRACKER_HEBER_READ_FAILED", "error": str(exc)},
        )
        return None

    if flow_df.empty:
        return []

    flow_df = _coerce_ticker_column(flow_df)
    ts_col = _first_existing_column(flow_df, ["flow_ts_utc", "ts_event", "timestamp", "created_at"])
    event_col = _first_existing_column(flow_df, ["event_id", "source_event_id", "id"])
    expiry_col = _first_existing_column(flow_df, ["expiry", "expiration", "exp_date"])
    put_call_col = _first_existing_column(flow_df, ["put_call", "type", "option_type", "right"])
    strike_col = _first_existing_column(flow_df, ["strike", "strike_price"])

    if ts_col is None or event_col is None or expiry_col is None or put_call_col is None or strike_col is None:
        return []

    pending: List[Dict[str, Any]] = []
    for _, row in flow_df.iterrows():
        event_id = row.get(event_col)
        ticker = row.get("ticker")
        flow_ts = _coerce_flow_ts(row.get(ts_col))
        expiry = _coerce_expiry(row.get(expiry_col))
        put_call = _normalize_put_call(row.get(put_call_col))
        strike = pd.to_numeric(row.get(strike_col), errors="coerce")

        if event_id is None or ticker is None or flow_ts is None:
            continue
        if flow_ts < start:
            continue
        if expiry is None or put_call is None or pd.isna(strike):
            continue

        option_symbol = _build_option_symbol(str(ticker).upper(), expiry, put_call, float(strike))
        minutes_since = (now_utc - flow_ts).total_seconds() / 60.0

        pending.append(
            {
                "event_id": str(event_id),
                "ticker": str(ticker).upper(),
                "option_symbol": option_symbol,
                "flow_ts_utc": flow_ts,
                "minutes_since_entry": minutes_since,
            }
        )

    pending.sort(key=lambda item: item["flow_ts_utc"], reverse=True)
    return pending[:limit]


async def get_existing_quotes(event_ids: List[str]) -> Dict[str, set]:
    """Get checkpoints already fetched for given events (in-process cache)."""
    if not event_ids:
        return {}

    existing: Dict[str, set] = {}
    for event_id in event_ids:
        checkpoints = _quote_checkpoint_cache.get(event_id)
        if checkpoints:
            existing[event_id] = set(checkpoints)
    return existing


async def store_quote(
    flow_event_id: str,
    option_symbol: str,
    underlying_ticker: str,
    checkpoint: str,
    ts_utc: datetime,
    quote_data: Dict[str, Any],
) -> None:
    """Record fetched checkpoints in memory while legacy tracker is active."""
    _ = (option_symbol, underlying_ticker, ts_utc, quote_data)
    checkpoints = _quote_checkpoint_cache.setdefault(flow_event_id, set())
    checkpoints.add(checkpoint)


async def run_quote_tracker() -> None:
    """Main quote tracking loop."""
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    logger.warning(
        "Legacy local option quote tracker is active",
        extra={
            "event_type": "DEPRECATED_PIPELINE_ACTIVE",
            "pipeline": "orion.main_option_quote_tracker",
            "replacement_path": "heber.watch.poller + labels_alert_barriers/meta_label_features",
        },
    )
    enabled, control_key, control_raw = _legacy_label_pipeline_control()
    if not enabled:
        logger.warning(
            "Legacy local option quote tracker disabled by config",
            extra={
                "event_type": "DEPRECATED_PIPELINE_DISABLED",
                "pipeline": "orion.main_option_quote_tracker",
                "control": f"{control_key}={control_raw}",
            },
        )
        return
    logger.info("Option Quote Tracker starting (deprecated — Greeks fetching disabled)...")

    while not shutdown_event.is_set():
        try:
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
                quotes = {}  # Greeks connector removed; checkpoint data populated by Heber watch pipeline

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
