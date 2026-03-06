"""
Backfill script for label ML feature columns.

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
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from orion.clients.heber_reader import get_heber_reader
from orion.main_price_target_labeler import (
    get_darkpool_metrics as get_labeler_darkpool_metrics,
)
from orion.main_price_target_labeler import (
    get_earnings_proximity as get_labeler_earnings_proximity,
)
from orion.main_price_target_labeler import (
    get_entry_time_features as get_labeler_entry_time_features,
)
from orion.main_price_target_labeler import (
    get_flow_aggression as get_labeler_flow_aggression,
)
from orion.main_price_target_labeler import (
    get_flow_greeks as get_labeler_flow_greeks,
)
from orion.main_price_target_labeler import (
    get_gex_at_entry,
    get_max_pain_distance,
)
from orion.main_price_target_labeler import (
    get_institutional_flow_1w as get_labeler_institutional_flow_1w,
)
from orion.main_price_target_labeler import (
    get_iv_rank_at_entry as get_labeler_iv_rank_at_entry,
)
from orion.main_price_target_labeler import (
    get_market_tide_before_entry as get_labeler_market_tide_before_entry,
)
from orion.main_price_target_labeler import (
    get_p2_features as get_labeler_p2_features,
)
from orion.main_price_target_labeler import (
    get_p3_features as get_labeler_p3_features,
)
from orion.main_price_target_labeler import (
    get_phase1_bucket_features as get_labeler_phase1_bucket_features,
)
from orion.main_price_target_labeler import (
    get_regime_at_entry as get_labeler_regime_at_entry,
)
from orion.main_price_target_labeler import (
    get_rvol_metrics as get_labeler_rvol_metrics,
)
from orion.main_price_target_labeler import (
    get_sector_correlation_features as get_labeler_sector_correlation_features,
)
from orion.main_price_target_labeler import (
    get_ticker_info as get_labeler_ticker_info,
)
from orion.main_price_target_labeler import (
    get_underlying_price_at_entry as get_labeler_underlying_price_at_entry,
)
from orion.main_price_target_labeler import (
    get_underlying_price_at_offset as get_labeler_underlying_price_at_offset,
)
from orion.shared.db_utils import db_query, db_write
from orion.shared.logger import setup_struct_logger
from orion.storage.db import init_db
from orion.storage.watermarks import get_cursor_state, upsert_cursor_state

logger = setup_struct_logger("orion.backfill.ml_features")

BATCH_SIZE = 50
BACKFILL_CURSOR_KEY = "backfill_ml_features.heber_gold.cursor"


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
_ticker_info_cache: dict[str, dict[str, Any]] = {}


async def get_ticker_info(ticker: str) -> dict[str, Any]:
    """Fetch ticker info via shared labeler helper with local cache."""
    if ticker in _ticker_info_cache:
        return _ticker_info_cache[ticker]

    cache_entry: dict[str, Any] = {
        "sector": None,
        "next_earnings_date": None,
        "announce_time": None,
        "last_earnings_date": None,
    }

    try:
        labeler_info = await get_labeler_ticker_info(ticker)
        if isinstance(labeler_info, dict):
            cache_entry["sector"] = labeler_info.get("sector")
            cache_entry["next_earnings_date"] = labeler_info.get("next_earnings_date")
            cache_entry["announce_time"] = labeler_info.get("announce_time")
            cache_entry["last_earnings_date"] = labeler_info.get("last_earnings_date")
    except Exception as e:
        logger.debug(f"Failed to fetch ticker info for {ticker} via shared helper: {e}")

    _ticker_info_cache[ticker] = cache_entry
    return cache_entry


async def get_earnings_proximity(ticker: str, entry_ts: datetime) -> dict[str, Any]:
    """Get earnings proximity via shared labeler helper."""
    return await get_labeler_earnings_proximity(ticker, entry_ts)


def get_entry_time_features(entry_ts: datetime) -> dict[str, Any]:
    """Delegate to live labeler logic to keep backfill semantics aligned."""
    return get_labeler_entry_time_features(entry_ts)


async def get_flow_greeks(event_id: str) -> dict[str, float | None]:
    """Get flow Greeks/volume/OI.

    Delegates to the shared price-target labeler helper so backfill and
    live labeling use one implementation contract.
    """
    return await get_labeler_flow_greeks(event_id)


async def get_underlying_price_at_entry(ticker: str, entry_ts: datetime) -> float | None:
    """Get underlying stock price at entry time.

    Delegates to the shared price-target labeler helper so backfill and
    live labeling use the same Heber-first source contract.
    """
    return await get_labeler_underlying_price_at_entry(ticker, entry_ts)


async def get_underlying_price_at_offset(ticker: str, entry_ts: datetime, hours: int) -> float | None:
    """Get underlying stock price at offset from entry.

    Delegates to the shared price-target labeler helper so backfill and
    live labeling use the same Heber-first source contract.
    """
    return await get_labeler_underlying_price_at_offset(ticker, entry_ts, hours)


async def get_phase1_bucket_features(ticker: str, entry_ts: datetime, dte: int) -> dict[str, Any]:
    """Get phase-1 bucket features via shared labeler helper."""
    return await get_labeler_phase1_bucket_features(ticker, entry_ts, dte)


async def get_sector_correlation_features(ticker: str, entry_ts: datetime) -> dict[str, Any]:
    """Get sector-correlation context via shared labeler helper."""
    return await get_labeler_sector_correlation_features(ticker, entry_ts)


async def get_iv_rank_at_entry(ticker: str, entry_ts: datetime) -> float | None:
    """Get IV rank at entry via shared labeler helper."""
    return await get_labeler_iv_rank_at_entry(ticker, entry_ts)


async def get_p2_features(ticker: str, option_chain: str, entry_ts: datetime) -> dict[str, Any]:
    """Get P2 option features via shared labeler helper."""
    return await get_labeler_p2_features(ticker, option_chain, entry_ts)


async def get_p3_features(ticker: str, option_chain: str, expiry: str, entry_ts: datetime) -> dict[str, Any]:
    """Get P3 option features via shared labeler helper."""
    return await get_labeler_p3_features(ticker, option_chain, expiry, entry_ts)


async def get_darkpool_metrics(ticker: str, entry_ts: datetime) -> dict[str, Any]:
    """Get darkpool metrics via shared labeler helper."""
    return await get_labeler_darkpool_metrics(ticker, entry_ts)


async def get_rvol_metrics(ticker: str, entry_ts: datetime) -> dict[str, Any]:
    """Get RVOL metrics via shared labeler helper."""
    return await get_labeler_rvol_metrics(ticker, entry_ts)


async def get_flow_aggression(ticker: str, entry_ts: datetime) -> dict[str, Any]:
    """Get flow aggression metrics via shared labeler helper."""
    return await get_labeler_flow_aggression(ticker, entry_ts)


async def get_institutional_flow_1w(ticker: str, entry_ts: datetime) -> float | None:
    """Get institutional flow aggregate via shared labeler helper."""
    return await get_labeler_institutional_flow_1w(ticker, entry_ts)


async def get_market_tide_before_entry(entry_ts: datetime, minutes: int = 30) -> dict[str, Any]:
    """Get market tide context via shared labeler helper."""
    return await get_labeler_market_tide_before_entry(entry_ts, minutes=minutes)


async def get_regime_at_entry(entry_ts: datetime) -> dict[str, Any]:
    """Get regime context via shared labeler helper."""
    return await get_labeler_regime_at_entry(entry_ts)


async def get_records_to_backfill(
    limit: int = 1000,
    after_entry_ts: datetime | None = None,
    after_event_id: str | None = None,
) -> list[dict[str, Any]]:
    """Get records missing ML feature columns from Heber Gold datasets."""
    outcomes_df = await _read_heber_gold_dataset("labels_alert_barriers")
    if outcomes_df.empty:
        return []

    features_df = await _read_heber_gold_dataset("meta_label_features")
    outcomes_norm = _normalize_outcomes_for_backfill(outcomes_df)
    if outcomes_norm.empty:
        return []

    features_norm = _normalize_features_for_backfill(features_df)
    merged = outcomes_norm.merge(features_norm, on="event_id", how="left", indicator=True)

    missing_columns = [
        "entry_hour",
        "minutes_to_close",
        "overnight_gap_pct",
        "gex_at_entry",
        "oi_change_1d",
    ]
    missing_mask = merged["_merge"] != "both"
    for column in missing_columns:
        if column not in merged.columns:
            missing_mask = missing_mask | True
        else:
            missing_mask = missing_mask | merged[column].isna()

    candidates = merged[missing_mask].copy()
    if candidates.empty:
        return []

    if after_entry_ts is not None and after_event_id is not None:
        candidates = candidates[
            (candidates["entry_ts"] > after_entry_ts)
            | ((candidates["entry_ts"] == after_entry_ts) & (candidates["event_id"] > after_event_id))
        ]
    elif after_entry_ts is not None:
        candidates = candidates[candidates["entry_ts"] >= after_entry_ts]

    if candidates.empty:
        return []

    candidates = candidates.sort_values(["entry_ts", "event_id"], ascending=[True, True]).head(limit)
    columns = ["event_id", "ticker", "entry_ts", "expiry", "dte", "option_chain"]
    for column in columns:
        if column not in candidates.columns:
            candidates[column] = None

    records = candidates[columns].to_dict("records")
    return [dict(record) for record in records]


def _first_existing_column(df: pd.DataFrame, names: list[str]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None


def _coerce_dataframe(payload: Any) -> pd.DataFrame:
    if isinstance(payload, pd.DataFrame):
        return payload.copy()
    if isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    if isinstance(payload, dict):
        return pd.DataFrame([payload])
    return pd.DataFrame()


async def _read_heber_gold_dataset(dataset: str) -> pd.DataFrame:
    now = datetime.now(UTC)
    reader = get_heber_reader()
    try:
        payload = await asyncio.to_thread(
            reader.read_gold_features,
            dataset=dataset,
            asof_time=now,
        )
    except Exception as exc:
        logger.warning(f"Failed to read Heber gold dataset {dataset}: {exc}")
        return pd.DataFrame()
    return _coerce_dataframe(payload)


def _normalize_ticker_series(df: pd.DataFrame) -> pd.Series:
    ticker_col = _first_existing_column(df, ["ticker", "underlying", "symbol", "instrument_key"])
    if ticker_col is None:
        return pd.Series(index=df.index, dtype=object)
    series = df[ticker_col].astype(str)
    if ticker_col == "instrument_key":
        series = series.str.split(":").str[-1]
    return series.str.upper()


def _normalize_outcomes_for_backfill(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["event_id", "ticker", "entry_ts", "expiry", "dte", "option_chain"])

    event_col = _first_existing_column(df, ["alert_id", "event_id", "watch_id", "instrument_key"])
    ts_col = _first_existing_column(df, ["entry_ts", "alert_time", "ts_event", "ts_available"])
    expiry_col = _first_existing_column(df, ["expiry", "expiry_date"])
    dte_col = _first_existing_column(df, ["dte", "days_to_expiry"])
    option_col = _first_existing_column(df, ["option_chain", "occ_symbol", "contract_symbol", "option_symbol"])

    event_series = df[event_col].astype(str) if event_col else pd.Series(index=df.index, dtype=object)
    ts_series = (
        pd.to_datetime(df[ts_col], utc=True, errors="coerce")
        if ts_col
        else pd.Series(index=df.index, dtype="datetime64[ns, UTC]")
    )
    ticker_series = _normalize_ticker_series(df)
    expiry_series = df[expiry_col] if expiry_col else pd.Series(index=df.index, dtype=object)
    dte_series = pd.to_numeric(df[dte_col], errors="coerce") if dte_col else pd.Series(index=df.index, dtype="float64")
    option_series = df[option_col] if option_col else pd.Series(index=df.index, dtype=object)

    normalized = pd.DataFrame(
        {
            "event_id": event_series,
            "ticker": ticker_series,
            "entry_ts": ts_series,
            "expiry": expiry_series,
            "dte": dte_series,
            "option_chain": option_series,
        }
    )
    normalized = normalized.dropna(subset=["event_id", "entry_ts"])
    normalized["event_id"] = normalized["event_id"].astype(str)
    return normalized


def _normalize_features_for_backfill(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=[
                "event_id",
                "entry_hour",
                "minutes_to_close",
                "overnight_gap_pct",
                "gex_at_entry",
                "oi_change_1d",
            ]
        )

    event_col = _first_existing_column(df, ["alert_id", "event_id", "watch_id", "instrument_key"])
    if event_col is None:
        return pd.DataFrame()

    normalized = pd.DataFrame({"event_id": df[event_col].astype(str)})
    normalized["entry_hour"] = pd.to_numeric(
        df[_first_existing_column(df, ["entry_hour", "hour_of_day"])]
        if _first_existing_column(df, ["entry_hour", "hour_of_day"])
        else None,
        errors="coerce",
    )
    normalized["minutes_to_close"] = pd.to_numeric(
        df[_first_existing_column(df, ["minutes_to_close"])]
        if _first_existing_column(df, ["minutes_to_close"])
        else None,
        errors="coerce",
    )
    normalized["overnight_gap_pct"] = pd.to_numeric(
        df[_first_existing_column(df, ["overnight_gap_pct"])]
        if _first_existing_column(df, ["overnight_gap_pct"])
        else None,
        errors="coerce",
    )
    normalized["gex_at_entry"] = pd.to_numeric(
        df[_first_existing_column(df, ["gex_at_entry", "gex"])]
        if _first_existing_column(df, ["gex_at_entry", "gex"])
        else None,
        errors="coerce",
    )
    normalized["oi_change_1d"] = pd.to_numeric(
        df[_first_existing_column(df, ["oi_change_1d"])] if _first_existing_column(df, ["oi_change_1d"]) else None,
        errors="coerce",
    )
    return normalized


async def _get_option_chain_for_event(event_id: str) -> str:
    """Best-effort Heber lookup for option chain by event_id."""
    event_id_str = str(event_id)
    try:
        flow_df = await asyncio.to_thread(
            get_heber_reader().read_flow,
            asof_time=datetime.now(UTC),
            start_time=datetime.now(UTC) - timedelta(days=365),
        )
    except Exception as e:
        logger.debug(f"Failed to load flow rows for option_chain lookup: {e}")
        return ""

    if flow_df is None or flow_df.empty:
        return ""

    event_col = _first_existing_column(flow_df, ["event_id", "source_event_id", "id"])
    option_col = _first_existing_column(flow_df, ["option_chain", "option_symbol", "contract", "contract_symbol"])
    ts_col = _first_existing_column(flow_df, ["flow_ts_utc", "ts_event", "timestamp", "created_at"])
    if event_col is None or option_col is None:
        return ""

    matched = flow_df[flow_df[event_col].astype(str) == event_id_str]
    if matched.empty:
        return ""

    if ts_col is not None:
        matched = matched.assign(_ts=pd.to_datetime(matched[ts_col], utc=True, errors="coerce")).sort_values("_ts")
    row = matched.iloc[-1]
    value = row.get(option_col)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value)


async def _load_backfill_cursor() -> tuple[datetime | None, str | None]:
    """Load persisted resume cursor (timestamp + event_id)."""

    async def query(session: Any) -> tuple[datetime | None, str | None]:
        cursor = await get_cursor_state(session, BACKFILL_CURSOR_KEY)
        if cursor is None:
            return None, None
        return cursor.last_seen_ts_utc, cursor.last_seen_id

    return await db_query(query)


async def _save_backfill_cursor(entry_ts: datetime, event_id: str | None) -> None:
    """Persist resume cursor."""

    async def write(session: Any) -> None:
        await upsert_cursor_state(
            session,
            key=BACKFILL_CURSOR_KEY,
            last_seen_ts_utc=entry_ts,
            last_seen_id=event_id,
        )

    await db_write(write)


async def update_ml_features(record: dict[str, Any]) -> bool:
    """Update ML feature columns for a record."""
    event_id = record["event_id"]
    ticker = record["ticker"]
    entry_ts = record["entry_ts"]

    updates: dict[str, Any] = {}

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

    earnings_info = await get_earnings_proximity(ticker, entry_ts)
    updates["days_to_earnings"] = earnings_info.get("days_to_earnings")
    updates["is_post_earnings"] = earnings_info.get("is_post_earnings")

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

    option_chain = record.get("option_chain", "")
    if not option_chain:
        option_chain = await _get_option_chain_for_event(event_id)
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

    updates["iv_rank_at_entry"] = await get_iv_rank_at_entry(ticker, entry_ts)

    if not updates:
        return False

    logger.warning(
        "Skipping legacy local ML feature backfill write; label storage is centralized",
        extra={"event_type": "DEPRECATED_LOCAL_WRITE_SKIPPED", "event_id": event_id},
    )
    return False


async def run_backfill(batch_size: int = BATCH_SIZE, limit: int = 1000) -> None:
    """Run the backfill job."""
    await init_db()

    logger.info(f"Starting ML features backfill with batch_size={batch_size}, limit={limit}")

    total_processed = 0
    total_updated = 0
    after_entry_ts, after_event_id = await _load_backfill_cursor()

    if after_entry_ts is not None:
        logger.info(
            "Resuming backfill from persisted cursor ts=%s event_id=%s",
            after_entry_ts.isoformat(),
            after_event_id,
        )

    while True:
        remaining = limit - total_processed
        if remaining <= 0:
            break

        records = await get_records_to_backfill(
            limit=min(batch_size, remaining),
            after_entry_ts=after_entry_ts,
            after_event_id=after_event_id,
        )
        if not records:
            break

        for record in records:
            try:
                if await update_ml_features(record):
                    total_updated += 1
            except Exception as e:
                logger.error(f"Failed to update {record['event_id']}: {e}")

            total_processed += 1
            after_entry_ts = record.get("entry_ts")
            after_event_id = record.get("event_id")
            if after_entry_ts is not None:
                await _save_backfill_cursor(after_entry_ts, after_event_id)

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
