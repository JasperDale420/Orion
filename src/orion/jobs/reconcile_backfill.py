import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy import func, select

from orion.clients.heber_reader import get_heber_reader
from orion.core.logging_config import setup_logging
from orion.storage.db import async_session_factory
from orion.storage.models import BronzeEvent

logger = logging.getLogger(__name__)
_PREFER_HEBER_FALSE_VALUES = {"0", "false", "no", "off", "n"}


@dataclass(frozen=True)
class ReconciliationDataset:
    name: str
    bronze_event_type: str


DATASET_SPECS: tuple[ReconciliationDataset, ...] = (
    ReconciliationDataset(
        name="ALPACA_BAR_1M",
        bronze_event_type="ALPACA_BAR_1M",
    ),
    ReconciliationDataset(
        name="UW_FLOW",
        bronze_event_type="UW_FLOW",
    ),
    ReconciliationDataset(
        name="UW_DARKPOOL",
        bronze_event_type="UW_DARKPOOL",
    ),
)


def _prefer_heber_source() -> bool:
    raw = os.getenv("ORION_RECONCILE_BACKFILL_PREFER_HEBER", "1").strip().lower()
    return raw not in _PREFER_HEBER_FALSE_VALUES


def _first_existing_column(df: pd.DataFrame, names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None


def _normalize_ticker(value: Any) -> str | None:
    if value is None:
        return None
    ticker = str(value).strip().upper()
    if not ticker:
        return None
    if ":" in ticker:
        ticker = ticker.split(":")[-1]
    return ticker or None


def _to_counts_map(df: pd.DataFrame) -> dict[tuple[str, str], int]:
    if df.empty:
        return {}
    ticker_col = _first_existing_column(df, ("ticker", "symbol", "underlying", "instrument_key"))
    ts_col = _first_existing_column(df, ("bar_start_ts", "bar_start_ts_utc", "flow_ts_utc", "dark_ts_utc", "ts_event"))
    if ticker_col is None or ts_col is None:
        return {}

    work = pd.DataFrame(
        {
            "ticker": df[ticker_col].map(_normalize_ticker),
            "event_ts": pd.to_datetime(df[ts_col], utc=True, errors="coerce"),
        }
    )
    work = work.dropna(subset=["ticker", "event_ts"])
    if work.empty:
        return {}

    grouped = work.groupby(["ticker", work["event_ts"].dt.date], as_index=False).size()
    counts: dict[tuple[str, str], int] = {}
    for _, row in grouped.iterrows():
        counts[(str(row["ticker"]), str(row["event_ts"]))] = int(row["size"])
    return counts


async def _heber_counts_for_spec(
    spec: ReconciliationDataset, start_date: datetime
) -> dict[tuple[str, str], int] | None:
    reader = get_heber_reader()
    now = datetime.now(timezone.utc)
    try:
        if spec.name == "ALPACA_BAR_1M":
            frame = await asyncio.to_thread(
                reader.read_bars,
                symbols=[],
                asof_time=now,
                start_time=start_date,
            )
        elif spec.name == "UW_FLOW":
            frame = await asyncio.to_thread(
                reader.read_flow,
                asof_time=now,
                start_time=start_date,
            )
        elif spec.name == "UW_DARKPOOL":
            frame = await asyncio.to_thread(
                reader.read_darkpool,
                asof_time=now,
                start_time=start_date,
            )
        else:
            return None
    except Exception as exc:
        logger.warning("Heber reconciliation read failed for %s: %s", spec.name, exc)
        return None

    if frame.empty:
        return {}
    return _to_counts_map(frame)


async def run_reconciliation(lookback_days: int = 7) -> None:
    """
    PRD 17.3: Reconciliation Job.
    Checks for missing bars between Bronze (Raw) and Silver (Normalized) layers.
    Triggers alerts (and eventually backfills) for discrepancies.
    """
    logger.info(f"Starting Data Reconciliation (Lookback: {lookback_days} days)...")

    start_date = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    async with async_session_factory() as session:
        try:
            total_discrepancies = 0
            checked_datasets = 0
            skipped_datasets = 0
            for spec in DATASET_SPECS:
                stmt_bronze = (
                    select(
                        BronzeEvent.ticker,
                        func.date(BronzeEvent.event_ts_utc).label("event_date"),
                        func.count(BronzeEvent.event_id).label("count"),
                    )
                    .where(BronzeEvent.event_type == spec.bronze_event_type)
                    .where(BronzeEvent.event_ts_utc >= start_date)
                    .group_by(BronzeEvent.ticker, func.date(BronzeEvent.event_ts_utc))
                )

                result_bronze = await session.execute(stmt_bronze)
                bronze_rows = result_bronze.all()
                bronze_counts = {(r.ticker, str(r.event_date)): r.count for r in bronze_rows}

                if not _prefer_heber_source():
                    logger.warning(
                        "Reconciliation skipped for %s: ORION_RECONCILE_BACKFILL_PREFER_HEBER disabled and "
                        "local SQL fallback has been removed.",
                        spec.name,
                    )
                    skipped_datasets += 1
                    continue

                heber_counts = await _heber_counts_for_spec(spec, start_date)
                if heber_counts is None:
                    logger.warning(
                        "Reconciliation skipped for %s: Heber source unavailable and local SQL fallback has "
                        "been removed.",
                        spec.name,
                    )
                    skipped_datasets += 1
                    continue

                silver_counts = heber_counts

                discrepancies = 0
                for key, b_count in bronze_counts.items():
                    ticker, date_str = key
                    s_count = silver_counts.get(key, 0)
                    if b_count != s_count:
                        logger.warning(
                            f"DATA GAP [{spec.name}]: {ticker} on {date_str} - Bronze: {b_count}, Silver: {s_count}"
                        )
                        discrepancies += 1

                for key, s_count in silver_counts.items():
                    if key not in bronze_counts:
                        ticker, date_str = key
                        logger.warning(
                            f"DATA ORPHAN [{spec.name}]: {ticker} on {date_str} exists in Silver ({s_count}) "
                            "but not Bronze."
                        )
                        discrepancies += 1

                checked_datasets += 1
                total_discrepancies += discrepancies
                if discrepancies == 0:
                    logger.info(f"Reconciliation [{spec.name}] complete: No gaps found.")
                else:
                    logger.warning(f"Reconciliation [{spec.name}] complete: Found {discrepancies} discrepancies.")

            if total_discrepancies == 0 and skipped_datasets == 0:
                logger.info(f"Reconciliation Complete: No gaps found across {checked_datasets} datasets.")
            else:
                logger.warning(
                    "Reconciliation Complete: Found %s discrepancies across %s datasets (%s skipped).",
                    total_discrepancies,
                    checked_datasets,
                    skipped_datasets,
                )
        except Exception as e:
            logger.error(f"Reconciliation Failed: {e}")


if __name__ == "__main__":
    # Setup simple logging for standalone run
    setup_logging()
    try:
        asyncio.run(run_reconciliation(lookback_days=30))
    except (KeyboardInterrupt, SystemExit):
        pass
