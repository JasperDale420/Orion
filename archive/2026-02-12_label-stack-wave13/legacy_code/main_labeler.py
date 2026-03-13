"""
Continuous Flow Labeling Service.

Runs alongside ingestion to label UW flow records with price outcomes.
Labels each flow with actual returns at 15m, 30m, 1h, 2h horizons.
"""

import asyncio
import signal
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from orion.clients.heber_reader import HeberReader
from orion.shared.db_utils import db_query, db_write
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db
from sqlalchemy import text

from orion.config import SystemSettings

logger = setup_struct_logger("orion.labeler")

# Configuration
BATCH_SIZE = 100
POLL_INTERVAL_SECONDS = 60
MIN_AGE_MINUTES = 130  # Only label flows older than 2h10m (so 2h price is available)
FLOW_LOOKBACK_HOURS = 72

_heber_reader = HeberReader()


def _legacy_label_pipeline_control() -> tuple[bool, str, str]:
    settings = SystemSettings()

    specific_key = "ORION_ENABLE_LEGACY_FLOW_LABELER"
    if settings.legacy_flow_labeler_enabled is not None:
        enabled = settings.legacy_flow_labeler_enabled
        raw = "true" if enabled else "false"
        return enabled, specific_key, raw

    global_key = "ORION_ENABLE_LEGACY_LABEL_PIPELINES"
    enabled = settings.legacy_label_pipelines_enabled
    raw = "true" if enabled else "false"
    return enabled, global_key, raw


def _legacy_label_pipelines_enabled() -> bool:
    enabled, _, _ = _legacy_label_pipeline_control()
    return enabled


@dataclass
class FlowRecord:
    event_id: str
    ticker: str
    flow_ts_utc: datetime
    expiry: str | None
    underlying_price: float
    option_price: float | None
    premium_usd: float | None
    aggressor: str | None
    put_call: str | None
    is_sweep: Any | None
    iv: float | None


def _coerce_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.to_pydatetime()


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_series_value(row: pd.Series, keys: list[str]) -> Any:
    for key in keys:
        if key in row and pd.notna(row[key]):
            return row[key]
    return None


def _normalize_flow_df(raw_df: pd.DataFrame, cutoff: datetime) -> list[FlowRecord]:
    if raw_df.empty:
        return []

    rows: list[FlowRecord] = []
    for _, row in raw_df.iterrows():
        event_id = _resolve_series_value(row, ["event_id", "source_event_id", "id"])
        ticker = _resolve_series_value(row, ["ticker", "symbol", "underlying"])
        flow_ts = _coerce_dt(_resolve_series_value(row, ["flow_ts_utc", "ts_event", "timestamp", "created_at"]))
        if not event_id or not ticker or not flow_ts:
            continue
        if flow_ts >= cutoff:
            continue

        underlying_price = (
            _coerce_float(_resolve_series_value(row, ["underlying_price", "spot_px", "spot_price"])) or 0.0
        )

        rows.append(
            FlowRecord(
                event_id=str(event_id),
                ticker=str(ticker),
                flow_ts_utc=flow_ts,
                expiry=_resolve_series_value(row, ["expiry"]),
                underlying_price=underlying_price,
                option_price=_coerce_float(_resolve_series_value(row, ["option_price", "price"])),
                premium_usd=_coerce_float(_resolve_series_value(row, ["premium_usd", "premium"])),
                aggressor=_resolve_series_value(row, ["aggressor", "side"]),
                put_call=_resolve_series_value(row, ["put_call", "type"]),
                is_sweep=_resolve_series_value(row, ["is_sweep", "sweep"]),
                iv=_coerce_float(_resolve_series_value(row, ["iv", "implied_volatility"])),
            )
        )

    rows.sort(key=lambda r: r.flow_ts_utc)
    return rows


async def _filter_unlabeled(records: list[FlowRecord], limit: int) -> list[FlowRecord]:
    if not records:
        return []

    candidate_ids = [r.event_id for r in records[: max(limit * 4, limit)]]

    async def query(session: Any) -> set[str]:
        stmt = text("SELECT event_id FROM flow_labels WHERE event_id = ANY(:event_ids)")
        result = await session.execute(stmt, {"event_ids": candidate_ids})
        return {row[0] for row in result.fetchall()}

    labeled_ids = await db_query(query)
    if not isinstance(labeled_ids, set):
        labeled_ids = set(labeled_ids or [])

    unlabeled = [r for r in records if r.event_id not in labeled_ids]
    return unlabeled[:limit]


async def get_unlabeled_flows(limit: int = BATCH_SIZE) -> list[Any]:
    """Get flow records that haven't been labeled yet, sourced from Heber."""
    now_utc = datetime.now(UTC)
    cutoff = now_utc - timedelta(minutes=MIN_AGE_MINUTES)
    start_time = cutoff - timedelta(hours=FLOW_LOOKBACK_HOURS)

    raw_df = _heber_reader.read_flow(
        asof_time=now_utc,
        start_time=start_time,
    )
    records = _normalize_flow_df(raw_df, cutoff=cutoff)
    return await _filter_unlabeled(records, limit=limit)


async def get_price_at_time(ticker: str, target_ts: datetime, tolerance_minutes: int = 10) -> float | None:
    """Get underlying price near target time using Heber bars."""
    window_start = target_ts - timedelta(minutes=tolerance_minutes)
    window_end = target_ts + timedelta(minutes=tolerance_minutes)

    bars = _heber_reader.read_bars(
        symbols=[ticker],
        asof_time=datetime.now(UTC),
        start_time=window_start,
        end_time=window_end,
    )
    if bars.empty:
        return None

    ts_col = None
    for candidate in ("ts_event", "bar_start_ts", "timestamp"):
        if candidate in bars.columns:
            ts_col = candidate
            break
    if ts_col is None:
        return None

    close_col = "close" if "close" in bars.columns else ("c" if "c" in bars.columns else None)
    if close_col is None:
        return None

    bars = bars.copy()
    bars["__ts"] = pd.to_datetime(bars[ts_col], utc=True, errors="coerce")
    bars = bars[(bars["__ts"] >= pd.Timestamp(window_start)) & (bars["__ts"] <= pd.Timestamp(window_end))]
    if bars.empty:
        return None

    bars["__delta"] = (bars["__ts"] - pd.Timestamp(target_ts)).abs()
    best = bars.sort_values("__delta").iloc[0]
    return _coerce_float(best[close_col])


def calculate_return(entry_price: float, exit_price: float) -> float | None:
    """Calculate percentage return."""
    if entry_price <= 0 or exit_price is None:
        return None
    return ((exit_price - entry_price) / entry_price) * 100


def classify_return(return_pct: float | None, threshold: float = 0.1) -> str:
    """Classify return as WIN/LOSS/FLAT."""
    if return_pct is None:
        return "UNKNOWN"
    if return_pct > threshold:
        return "WIN"
    elif return_pct < -threshold:
        return "LOSS"
    return "FLAT"


def parse_expiry(expiry_str: str | None) -> datetime | None:
    """Parse expiry string to datetime."""
    if not expiry_str:
        return None
    try:
        return datetime.strptime(expiry_str, "%Y-%m-%d").replace(tzinfo=UTC)
    except (ValueError, AttributeError):
        return None


def calculate_dte(flow_ts: datetime, expiry: datetime | None) -> int | None:
    """Calculate days to expiry."""
    if not expiry:
        return None
    dte = (expiry.date() - flow_ts.date()).days
    return max(0, dte)


def classify_trade_type(dte: int | None) -> str:
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


async def label_flow(flow: Any) -> dict[str, Any] | None:
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


async def persist_labels(labels: list[dict[str, Any]]) -> int:
    """Persist labeled records to database."""
    enabled, control_key, control_raw = _legacy_label_pipeline_control()
    if not enabled:
        logger.warning(
            "Skipping local flow label persistence because legacy pipeline is disabled",
            extra={
                "event_type": "DEPRECATED_PIPELINE_DISABLED",
                "pipeline": "orion.main_labeler",
                "control": f"{control_key}={control_raw}",
            },
        )
        return 0

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
    logger.warning(
        "Legacy local flow labeler is active",
        extra={
            "event_type": "DEPRECATED_PIPELINE_ACTIVE",
            "pipeline": "orion.main_labeler",
            "replacement_path": "heber.watch.writer labels_alert_barriers",
        },
    )
    enabled, control_key, control_raw = _legacy_label_pipeline_control()
    if not enabled:
        logger.warning(
            "Legacy local flow labeler disabled by config",
            extra={
                "event_type": "DEPRECATED_PIPELINE_DISABLED",
                "pipeline": "orion.main_labeler",
                "control": f"{control_key}={control_raw}",
            },
        )
        return
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
        except TimeoutError:
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
