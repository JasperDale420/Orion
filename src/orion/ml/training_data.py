"""Training data loading, normalization, and feature joining from Heber Gold datasets.

Handles the full pipeline from raw Heber Gold parquet data to a clean, joined
DataFrame ready for LightGBM training.
"""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np

from orion.clients.heber_reader import get_heber_reader
from orion.ml.feature_config import (
    CATEGORICAL_COLUMNS,
    EQUITY_GOLD_DATASETS,
    FEATURE_COLUMNS,
)
from orion.ml.heber_utils import coerce_dataframe as _coerce_dataframe
from orion.ml.heber_utils import first_existing_column as _first_existing_column
from orion.ml.heber_utils import generate_deterministic_event_ids as _generate_deterministic_event_ids
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.ml.training_data")


def _validate_heber_training_contract(outcomes_raw: Any, features_raw: Any) -> None:
    if outcomes_raw.empty and features_raw.empty:
        return

    required_outcome_families: dict[str, list[str]] = {
        "event_id": ["alert_id", "event_id", "watch_id", "instrument_key"],
        "entry_ts": ["entry_ts", "alert_time", "ts_event", "ts_available"],
        "outcome_or_hit_tp": ["outcome", "outcome_reason", "status", "hit_tp_first", "contract_hit_tp_first"],
        "trading_minutes_to_hit": ["trading_minutes_to_hit", "bars_to_hit"],
    }
    soft_feature_families: dict[str, list[str]] = {
        "event_id": ["alert_id", "event_id", "watch_id", "instrument_key"],
    }

    missing_outcome_families = [
        family
        for family, candidates in required_outcome_families.items()
        if _first_existing_column(outcomes_raw, candidates) is None
    ]
    missing_feature_families = [
        family
        for family, candidates in soft_feature_families.items()
        if _first_existing_column(features_raw, candidates) is None
    ]

    if not missing_outcome_families and not missing_feature_families:
        return

    if missing_outcome_families:
        message = f"Heber training contract mismatch: labels_alert_barriers missing {missing_outcome_families}"
        logger.error(
            message,
            extra={
                "event": "pattern_miner_heber_training_contract_mismatch",
                "missing_outcome_families": missing_outcome_families,
                "outcomes_columns": sorted(str(col) for col in outcomes_raw.columns),
            },
        )
        raise RuntimeError(message)

    if missing_feature_families:
        logger.warning(
            "Heber feature dataset missing event_id family; will generate deterministic IDs",
            extra={
                "event": "pattern_miner_heber_features_missing_event_id",
                "missing_feature_families": missing_feature_families,
                "features_columns": sorted(str(col) for col in features_raw.columns),
            },
        )


def _normalize_heber_outcomes(frame: Any) -> Any:
    import pandas as pd

    if frame.empty:
        return pd.DataFrame(
            columns=[
                "event_id",
                "entry_ts",
                "outcome",
                "hit_tp_first",
                "trading_minutes_to_hit",
                "bars_to_hit",
                "snapshot_count",
                "no_snapshot",
                "time_to_mfe_seconds",
                "time_to_mae_seconds",
                "mfe_mae_ratio",
                "excursion_velocity",
                "capture_efficiency",
            ]
        )

    event_column = _first_existing_column(frame, ["alert_id", "event_id", "watch_id", "instrument_key"])
    ts_column = _first_existing_column(frame, ["entry_ts", "alert_time", "ts_event", "ts_available"])
    outcome_column = _first_existing_column(frame, ["outcome", "outcome_reason", "status"])
    hit_tp_column = _first_existing_column(frame, ["hit_tp_first", "contract_hit_tp_first"])
    trading_minutes_column = _first_existing_column(frame, ["trading_minutes_to_hit", "bars_to_hit"])
    bars_to_hit_column = _first_existing_column(frame, ["bars_to_hit"])
    event_series = frame[event_column].astype(str) if event_column else pd.Series(index=frame.index, dtype=object)
    ts_series = (
        pd.to_datetime(frame[ts_column], utc=True, errors="coerce")
        if ts_column
        else pd.Series(index=frame.index, dtype="datetime64[ns, UTC]")
    )
    outcome_series = (
        frame[outcome_column].astype(str).str.lower() if outcome_column else pd.Series(index=frame.index, dtype=object)
    )
    hit_tp_series = (
        pd.to_numeric(frame[hit_tp_column], errors="coerce").fillna(0).astype(int)
        if hit_tp_column
        else (outcome_series == "hit_tp").astype(int)
    )
    trading_minutes_series = (
        pd.to_numeric(frame[trading_minutes_column], errors="coerce")
        if trading_minutes_column
        else pd.Series(index=frame.index, dtype="float64")
    )
    bars_to_hit_series = (
        pd.to_numeric(frame[bars_to_hit_column], errors="coerce")
        if bars_to_hit_column
        else pd.Series(index=frame.index, dtype="float64")
    )

    # Temporal excursion fields (soft — missing columns default to NaN)
    excursion_fields = [
        "time_to_mfe_seconds",
        "time_to_mae_seconds",
        "mfe_mae_ratio",
        "excursion_velocity",
        "capture_efficiency",
    ]
    excursion_series: dict[str, Any] = {}
    for field in excursion_fields:
        if field in frame.columns:
            excursion_series[field] = pd.to_numeric(frame[field], errors="coerce")
        else:
            excursion_series[field] = pd.Series(index=frame.index, dtype="float64")

    normalized = pd.DataFrame(
        {
            "event_id": event_series,
            "entry_ts": ts_series,
            "outcome": outcome_series,
            "hit_tp_first": hit_tp_series,
            "trading_minutes_to_hit": trading_minutes_series,
            "bars_to_hit": bars_to_hit_series,
            **excursion_series,
        }
    )
    normalized = normalized.dropna(subset=["event_id", "entry_ts"])
    normalized["event_id"] = normalized["event_id"].astype(str)
    return normalized


def _drop_no_snapshot_outcomes(dataframe: Any) -> tuple[Any, int]:
    # New Heber pipelines cleanly filter out invalid alerts;
    # "no snapshot" is no longer a concept passed to gold barriers.
    return dataframe, 0


def _normalize_heber_features(frame: Any) -> Any:
    import pandas as pd

    if frame.empty:
        return pd.DataFrame(columns=["event_id"])

    event_column = _first_existing_column(frame, ["alert_id", "event_id", "watch_id", "instrument_key"])
    if event_column is not None:
        normalized = pd.DataFrame({"event_id": frame[event_column].astype(str)})
    else:
        logger.warning(
            "No event_id column in features; generating deterministic IDs",
            extra={"event": "pattern_miner_features_synthetic_event_id"},
        )
        normalized = pd.DataFrame({"event_id": _generate_deterministic_event_ids(frame)})

    mapped_columns: dict[str, list[str]] = {
        "put_call": ["put_call"],
        "strike": ["strike"],
        "days_to_expiry": ["days_to_expiry", "dte"],
        "premium": ["premium"],
        "volume": ["volume"],
        "open_interest": ["open_interest"],
        "volume_oi_ratio": ["volume_oi_ratio"],
        "spot_price": ["spot_price", "underlying_price"],
        "contract_price": ["contract_price"],
        "moneyness": ["moneyness"],
        "log_moneyness": ["log_moneyness"],
        "delta": ["delta"],
        "gamma": ["gamma"],
        "theta": ["theta"],
        "vega": ["vega"],
        "iv": ["iv"],
        "underlying_30d_return": ["underlying_30d_return"],
        "underlying_5d_return": ["underlying_5d_return"],
        "underlying_1d_return": ["underlying_1d_return"],
        "realized_vol_20d": ["realized_vol_20d"],
        "iv_rank": ["iv_rank"],
        "hour_of_day": ["hour_of_day"],
        "minute_of_hour": ["minute_of_hour"],
        "day_of_week": ["day_of_week"],
        "minutes_since_open": ["minutes_since_open"],
        "minutes_to_close": ["minutes_to_close"],
        "alert_type": ["alert_type"],
        "side": ["side"],
        "aggressor": ["aggressor"],
        "is_bullish": ["is_bullish"],
        "is_bearish": ["is_bearish"],
        "is_sweep": ["is_sweep"],
        "is_block": ["is_block"],
        "is_unusual": ["is_unusual"],
    }

    for target, candidates in mapped_columns.items():
        source = _first_existing_column(frame, candidates)
        if source is None:
            normalized[target] = pd.NA
            continue
        normalized[target] = frame[source]

    for bool_col in ["is_bullish", "is_bearish", "is_sweep", "is_block", "is_unusual"]:
        normalized[bool_col] = normalized[bool_col].fillna(0)
        normalized[bool_col] = normalized[bool_col].map(
            lambda value: str(value).strip().lower() in {"1", "true", "t", "yes", "y", "on"}
        )

    return normalized


def _apply_trade_type_filter(dataframe: Any, trade_type_filter: str | None) -> Any:
    import pandas as pd

    if not trade_type_filter:
        return dataframe

    match = re.search(r"trade_type\s*=\s*'([^']+)'", trade_type_filter, flags=re.IGNORECASE)
    if match is None:
        return dataframe

    bucket = match.group(1).upper()
    days_to_expiry = pd.to_numeric(dataframe["days_to_expiry"], errors="coerce")
    if bucket == "0DTE":
        mask = days_to_expiry == 0
    elif bucket == "SHORT_SWING":
        mask = (days_to_expiry >= 1) & (days_to_expiry <= 2)
    elif bucket == "SWING":
        mask = (days_to_expiry >= 3) & (days_to_expiry <= 14)
    elif bucket == "POSITION":
        mask = days_to_expiry >= 15
    else:
        return dataframe

    return dataframe[mask]


def _extract_symbol_from_instrument_key(instrument_key_series: Any) -> Any:
    """Extract bare ticker symbol from instrument_key (e.g. 'equity:AAPL' -> 'AAPL')."""
    return instrument_key_series.astype(str).str.split(":").str[-1].str.upper()


def _join_equity_features(
    alert_df: Any,
    equity_df: Any,
    feature_cols: list[str],
    alert_symbol_col: str = "symbol",
    alert_ts_col: str = "entry_ts",
) -> Any:
    """Asof-join equity-level features to alert-level data.

    Matches on (underlying symbol, timestamp) with 1-day backward tolerance.
    """
    import pandas as pd

    if equity_df.empty or alert_df.empty:
        return alert_df

    equity_df = equity_df.copy()
    if "instrument_key" in equity_df.columns:
        equity_df["_join_symbol"] = _extract_symbol_from_instrument_key(equity_df["instrument_key"])
    elif "symbol" in equity_df.columns:
        equity_df["_join_symbol"] = equity_df["symbol"].astype(str).str.upper()
    else:
        logger.warning("Equity features missing instrument_key and symbol columns, skipping join")
        return alert_df

    equity_ts_col = "ts_event" if "ts_event" in equity_df.columns else None
    if equity_ts_col is None:
        for candidate in ["ts_available", "timestamp", "date"]:
            if candidate in equity_df.columns:
                equity_ts_col = candidate
                break
    if equity_ts_col is None:
        logger.warning("Equity features missing timestamp column, skipping join")
        return alert_df

    available_feature_cols = [c for c in feature_cols if c in equity_df.columns]
    if not available_feature_cols:
        return alert_df

    equity_subset = equity_df[["_join_symbol", equity_ts_col] + available_feature_cols].copy()
    equity_subset[equity_ts_col] = pd.to_datetime(equity_subset[equity_ts_col], utc=True, errors="coerce")
    equity_subset = equity_subset.dropna(subset=[equity_ts_col])
    equity_subset = equity_subset.sort_values(equity_ts_col)

    alert_df = alert_df.copy()

    if alert_symbol_col in alert_df.columns:
        alert_df["_join_symbol"] = alert_df[alert_symbol_col].astype(str).str.upper()
    elif "ticker" in alert_df.columns:
        alert_df["_join_symbol"] = alert_df["ticker"].astype(str).str.upper()
    elif "instrument_key" in alert_df.columns:
        alert_df["_join_symbol"] = _extract_symbol_from_instrument_key(alert_df["instrument_key"])
    else:
        logger.warning("Alert data missing symbol/ticker/instrument_key, skipping equity join")
        return alert_df

    alert_df["_join_ts"] = pd.to_datetime(alert_df[alert_ts_col], utc=True, errors="coerce")
    alert_df = alert_df.sort_values("_join_ts")

    equity_subset = equity_subset.rename(columns={equity_ts_col: "_join_ts"})

    merged = pd.merge_asof(
        alert_df,
        equity_subset,
        on="_join_ts",
        by="_join_symbol",
        tolerance=pd.Timedelta("1D"),
        direction="backward",
    )

    merged = merged.drop(columns=["_join_symbol", "_join_ts"], errors="ignore")
    return merged


async def _load_equity_gold_features(reader: Any, now: Any) -> dict[str, Any]:
    """Load equity-level Gold datasets with graceful degradation."""
    import pandas as pd

    equity_datasets: dict[str, Any] = {}

    for dataset_name in EQUITY_GOLD_DATASETS:
        try:
            payload = await asyncio.to_thread(
                reader.read_gold_features,
                dataset=dataset_name,
                asof_time=now,
            )
            df = _coerce_dataframe(payload)
            equity_datasets[dataset_name] = df
            if not df.empty:
                logger.info(
                    f"Loaded equity Gold dataset {dataset_name}: {len(df)} rows",
                    extra={
                        "event": "equity_gold_loaded",
                        "dataset": dataset_name,
                        "row_count": len(df),
                    },
                )
            else:
                logger.info(
                    f"Equity Gold dataset {dataset_name} is empty",
                    extra={"event": "equity_gold_empty", "dataset": dataset_name},
                )
        except Exception as exc:
            logger.warning(
                f"Equity Gold dataset {dataset_name} unavailable: {exc}",
                extra={
                    "event": "equity_gold_unavailable",
                    "dataset": dataset_name,
                    "error": str(exc),
                },
            )
            equity_datasets[dataset_name] = pd.DataFrame()

    return equity_datasets


async def prefetch_heber_gold_data(
    max_retries: int = 3,
    retry_delay_seconds: float = 5.0,
) -> tuple[Any, Any, dict[str, Any]] | None:
    """Read labels_alert_barriers, meta_label_features, and equity Gold datasets once.

    Retries on empty results to handle transient volume-mount or I/O failures.
    Returns ``(outcomes_raw, features_raw, equity_gold)`` or ``None`` on failure.
    """
    reader = get_heber_reader()
    now = datetime.now(UTC)

    for attempt in range(1, max_retries + 1):
        try:
            outcomes_payload = await asyncio.to_thread(
                reader.read_gold_features,
                dataset="labels_alert_barriers",
                asof_time=now,
            )
            features_payload = await asyncio.to_thread(
                reader.read_gold_features,
                dataset="meta_label_features",
                asof_time=now,
            )
        except Exception as exc:
            logger.warning(
                f"Failed to read Heber gold training datasets (attempt {attempt}/{max_retries}): {exc}",
                extra={
                    "event": "pattern_miner_heber_training_read_failed",
                    "training_source": "heber_gold",
                    "attempt": attempt,
                    "max_retries": max_retries,
                },
            )
            if attempt < max_retries:
                await asyncio.sleep(retry_delay_seconds * attempt)
                continue
            return None

        outcomes_raw = _coerce_dataframe(outcomes_payload)
        features_raw = _coerce_dataframe(features_payload)

        if not outcomes_raw.empty:
            logger.info(
                f"Prefetched Heber gold training data: {len(outcomes_raw)} outcomes, {len(features_raw)} features",
                extra={
                    "event": "pattern_miner_heber_prefetch_success",
                    "outcomes_count": len(outcomes_raw),
                    "features_count": len(features_raw),
                    "attempt": attempt,
                },
            )
            equity_gold = await _load_equity_gold_features(reader, now)
            return outcomes_raw, features_raw, equity_gold

        logger.warning(
            f"Heber gold data empty on attempt {attempt}/{max_retries}",
            extra={
                "event": "pattern_miner_heber_prefetch_empty",
                "attempt": attempt,
                "max_retries": max_retries,
                "data_root": str(reader.data_root),
                "data_root_exists": reader.data_root.exists(),
                "gold_dir_exists": (reader.data_root / "gold").exists(),
            },
        )
        if attempt < max_retries:
            await asyncio.sleep(retry_delay_seconds * attempt)

    logger.error(
        "Heber gold data empty after all retries",
        extra={
            "event": "pattern_miner_heber_prefetch_exhausted",
            "max_retries": max_retries,
        },
    )
    return None


async def fetch_training_data(
    window_days: int = 30,
    min_samples: int = 100,
    trade_type_filter: str | None = None,
    quick_winner_seconds: int = 3600,
    prefetched: tuple[Any, Any, dict[str, Any]] | None = None,
) -> tuple[Any, list[str]]:
    """Fetch training data for pattern miner.

    Returns:
        Tuple of (pandas DataFrame, list of feature names)
    """
    import pandas as pd

    if prefetched is not None:
        outcomes_raw, features_raw, equity_gold = prefetched
    else:
        result = await prefetch_heber_gold_data()
        if result is None:
            return None, []
        outcomes_raw, features_raw, equity_gold = result

    cutoff = datetime.now(UTC) - timedelta(days=window_days)

    logger.info(f"Raw outcomes size: {len(outcomes_raw)}")
    logger.info(f"Raw features size: {len(features_raw)}")

    _validate_heber_training_contract(outcomes_raw, features_raw)
    outcomes = _normalize_heber_outcomes(outcomes_raw)
    features = _normalize_heber_features(features_raw)

    logger.info(f"Normalized outcomes size: {len(outcomes)}")
    logger.info(f"Normalized features size: {len(features)}")
    outcomes, dropped_no_snapshot = _drop_no_snapshot_outcomes(outcomes)
    if dropped_no_snapshot:
        logger.warning(
            f"Dropped {dropped_no_snapshot} no-snapshot outcomes from pattern-miner training set",
            extra={
                "event": "pattern_miner_drop_no_snapshot_outcomes",
                "dropped_rows": dropped_no_snapshot,
                "remaining_rows": len(outcomes),
            },
        )
    if outcomes.empty:
        logger.warning("No Heber outcomes available for pattern-miner training")
        return None, []

    merged = outcomes.merge(features, on="event_id", how="left")
    merged = merged[pd.to_datetime(merged["entry_ts"], utc=True, errors="coerce") >= cutoff]
    merged = _apply_trade_type_filter(merged, trade_type_filter)

    if len(merged) < min_samples:
        logger.warning(f"Insufficient Heber samples: {len(merged)} < {min_samples}")
        return None, []

    # Asof-join equity-level Gold features to alert-level training data
    equity_joined_count = 0
    for dataset_name, feature_cols in EQUITY_GOLD_DATASETS.items():
        equity_df = equity_gold.get(dataset_name, pd.DataFrame())
        if not equity_df.empty:
            pre_cols = set(merged.columns)
            merged = _join_equity_features(
                alert_df=merged,
                equity_df=equity_df,
                feature_cols=feature_cols,
                alert_symbol_col="symbol" if "symbol" in merged.columns else "ticker",
                alert_ts_col="entry_ts",
            )
            new_cols = set(merged.columns) - pre_cols
            if new_cols:
                equity_joined_count += len(new_cols)
                logger.info(
                    f"Joined {len(new_cols)} features from {dataset_name}",
                    extra={
                        "event": "equity_features_joined",
                        "dataset": dataset_name,
                        "new_columns": sorted(new_cols),
                    },
                )

    if equity_joined_count > 0:
        logger.info(
            f"Total equity features joined: {equity_joined_count}",
            extra={"event": "equity_features_join_summary", "total_joined": equity_joined_count},
        )
    else:
        logger.warning(
            "No equity Gold features available; training with alert-level features only",
            extra={"event": "equity_features_unavailable_training"},
        )

    # Compute derived features (iv_vs_realized, vega_theta_ratio, etc.)
    from orion.ml.derived_features import compute_derived_features_batch

    merged = compute_derived_features_batch(merged)

    # Compute runtime-derived flow normalization ratios
    for num_col, denom_col, out_col in [
        ("premium", "adv_premium_20d", "premium_vs_adv"),
        ("volume", "adv_volume_20d", "volume_vs_adoi"),
        ("open_interest", "adv_oi_20d", "relative_oi_buildup"),
    ]:
        if num_col in merged.columns and denom_col in merged.columns:
            num = pd.to_numeric(merged[num_col], errors="coerce")
            denom = pd.to_numeric(merged[denom_col], errors="coerce")
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = num / denom
            ratio[~np.isfinite(ratio)] = np.nan
            merged[out_col] = ratio
        else:
            merged[out_col] = pd.NA

    feature_names = FEATURE_COLUMNS + CATEGORICAL_COLUMNS
    for feature_name in feature_names:
        if feature_name not in merged.columns:
            merged[feature_name] = pd.NA

    hit_tp = pd.to_numeric(merged["hit_tp_first"], errors="coerce").fillna(0).astype(int) > 0
    outcome = merged["outcome"].astype(str).str.lower()
    hit_stop = outcome.isin({"hit_sl", "stop_loss", "stop"})

    merged["target_hit_target_50"] = hit_tp.astype(int)
    merged["target_avoid_stop"] = (~hit_stop).astype(int)
    merged["target_hit_target_100"] = hit_tp.astype(int)
    trading_minutes = pd.to_numeric(merged["trading_minutes_to_hit"], errors="coerce").fillna(float("inf"))
    merged["target_quick_winner"] = (hit_tp & (trading_minutes * 60 <= quick_winner_seconds)).astype(int)

    columns = (
        ["event_id", "entry_ts"]
        + FEATURE_COLUMNS
        + CATEGORICAL_COLUMNS
        + ["target_hit_target_50", "target_avoid_stop", "target_hit_target_100", "target_quick_winner"]
    )
    merged = merged.reindex(columns=columns)

    logger.info(
        f"Fetched {len(merged)} pattern-miner samples from Heber gold datasets",
        extra={
            "event": "pattern_miner_heber_training_loaded",
            "sample_count": len(merged),
            "training_source": "heber_gold",
        },
    )
    return merged, feature_names
