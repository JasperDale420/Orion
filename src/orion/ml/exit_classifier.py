"""
Exit Classifier v2 - Bucket-Specific Exit Timing.

ML classifier to predict optimal exit timing for open positions.
Uses legacy local label-table data with bucket-appropriate checkpoints.

Buckets and their time horizons:
- 0DTE: Minutes (5m, 10m, 15m, 30m, 1h)
- SHORT_SWING: Hours (30m, 1h, 2h, 4h, 8h)
- SWING: Hours to days (1h, 4h, 8h, EOD, 1d, 2d)
- POSITION: Days to weeks (1d, 2d, 3d, 1w)
"""

import asyncio
import pickle
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isnan
from typing import Any

import numpy as np
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

from orion.clients.heber_reader import get_heber_reader
from orion.config import SystemSettings, system_settings
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.ml.exit_classifier")

MODEL_DIR = system_settings.model_dir

EXIT_FEATURE_NAMES: tuple[str, ...] = (
    "current_return_pct",
    "time_held_hours",
    "delta_at_checkpoint",
    "gamma_at_checkpoint",
    "theta_at_checkpoint",
    "iv_at_checkpoint",
    "dte_at_checkpoint",
    "time_value_pct",
    "theta_decay_pct",
    "premium_usd",
    "dte_at_entry",
    "is_sweep",
    "iv_rank_at_entry",
    "vix_at_entry",
    "gex_at_entry",
    "market_tide_30m",
    "delta_at_entry",
    "theta_at_entry",
    "iv_at_entry",
    "ask_side_ratio",
    "window_call_put_imbalance_1h",
    "window_sweep_ratio_1h",
    "window_flow_count_1h",
    "window_call_put_imbalance_1d",
    "window_sweep_ratio_1d",
    "window_dp_volume_1d",
    "window_call_put_ratio_1d",
    "window_call_put_imbalance_1w",
    "window_sweep_ratio_1w",
    "window_call_put_ratio_1w",
)


def _legacy_exit_training_control() -> tuple[bool, str, str]:
    settings = SystemSettings()

    specific_key = "ORION_ENABLE_LEGACY_EXIT_CLASSIFIER_TRAINING"
    if settings.legacy_exit_classifier_training_enabled is not None:
        enabled = settings.legacy_exit_classifier_training_enabled
        raw = "true" if enabled else "false"
        return enabled, specific_key, raw

    global_key = "ORION_ENABLE_LEGACY_LABEL_PIPELINES"
    enabled = settings.legacy_label_pipelines_enabled
    raw = "true" if enabled else "false"
    return enabled, global_key, raw


def _legacy_exit_training_enabled() -> bool:
    enabled, _, _ = _legacy_exit_training_control()
    return enabled


def _exit_classifier_training_source() -> str:
    settings = SystemSettings()
    raw_source = (settings.exit_classifier_training_source or "heber_gold").strip().lower()

    if raw_source in {"heber", "heber_gold", "gold"}:
        return "heber_gold"
    if raw_source in {"legacy", "legacy_sql", "local", "local_sql"}:
        logger.warning(
            "Legacy exit-classifier SQL training source is decommissioned; falling back to heber_gold",
            extra={
                "event": "exit_classifier_training_source_legacy_decommissioned",
                "training_source": raw_source,
                "fallback_training_source": "heber_gold",
            },
        )
        return "heber_gold"

    logger.warning(
        f"Invalid exit-classifier training source '{raw_source}', falling back to heber_gold",
        extra={
            "event": "exit_classifier_training_source_invalid",
            "training_source": raw_source,
            "fallback_training_source": "heber_gold",
        },
    )
    return "heber_gold"


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Safely convert value to float, handling None and string values."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _is_truthy(val: Any) -> bool:
    """Normalize bool-like values from DB payloads."""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        return val.strip().lower() in {"1", "true", "t", "yes", "y"}
    return False


def _can_train_with_labels(y: np.ndarray, min_samples: int = 100) -> tuple[bool, str]:
    """Validate label distribution before train/test split."""
    sample_count = len(y)
    if sample_count < min_samples:
        return False, f"insufficient samples: {sample_count} < {min_samples}"

    unique_values, counts = np.unique(y, return_counts=True)
    if len(unique_values) < 2:
        return False, "single class labels"

    if int(np.min(counts)) < 2:
        return False, "at least one class has fewer than 2 samples"

    return True, ""


def _empty_training_arrays(feature_count: int) -> tuple[np.ndarray, np.ndarray]:
    """Return stable empty matrix/vector outputs for training datasets."""
    return (
        np.empty((0, feature_count), dtype=float),
        np.empty((0,), dtype=int),
    )


def _clear_price_target_label_schema_cache() -> None:
    """Compatibility no-op: legacy local schema cache is decommissioned."""
    return None


def _required_price_target_columns_for_bucket(checkpoints: list[tuple[str, float, str]]) -> set[str]:
    """Build the required column set for a bucket training query."""
    required = {
        "ticker",
        "entry_ts",
        "premium_usd",
        "dte",
        "is_sweep",
        "iv_rank_at_entry",
        "vix_at_entry",
        "trend_regime_at_entry",
        "vol_regime_at_entry",
        "gex_at_entry",
        "market_tide_30m",
        "delta_at_entry",
        "gamma_at_entry",
        "theta_at_entry",
        "iv_at_entry",
        "ask_side_ratio",
        "max_return_pct",
        "max_drawdown_pct",
        "trade_type",
    }
    for suffix, _hours, _desc in checkpoints:
        required.add(f"return_at_{suffix}")
        required.add(f"delta_at_{suffix}")
        required.add(f"gamma_at_{suffix}")
        required.add(f"theta_at_{suffix}")
        required.add(f"iv_at_{suffix}")
        required.add(f"dte_at_{suffix}")
        required.add(f"time_value_pct_at_{suffix}")
        required.add(f"theta_decay_pct_at_{suffix}")
    return required


async def _load_price_target_label_columns(force_refresh: bool = False) -> set[str]:
    """Compatibility no-op: local label-table schema preflight is decommissioned."""
    _ = force_refresh
    return set()


def _group_missing_columns_by_family(
    missing_columns: set[str], checkpoints: list[tuple[str, float, str]]
) -> dict[str, list[str]]:
    """Group missing columns into actionable diagnostics families."""
    grouped: dict[str, set[str]] = {
        "entry_context": set(),
        "outcome": set(),
        "checkpoint_returns": set(),
        "checkpoint_greeks": set(),
        "checkpoint_time_decay": set(),
        "other": set(),
    }
    checkpoint_suffixes = {suffix for suffix, _hours, _desc in checkpoints}

    for column in missing_columns:
        if column in {"max_return_pct", "max_drawdown_pct"}:
            grouped["outcome"].add(column)
            continue
        if column in {
            "ticker",
            "entry_ts",
            "premium_usd",
            "dte",
            "is_sweep",
            "iv_rank_at_entry",
            "vix_at_entry",
            "trend_regime_at_entry",
            "vol_regime_at_entry",
            "gex_at_entry",
            "market_tide_30m",
            "delta_at_entry",
            "gamma_at_entry",
            "theta_at_entry",
            "iv_at_entry",
            "ask_side_ratio",
            "trade_type",
        }:
            grouped["entry_context"].add(column)
            continue
        if any(column == f"return_at_{suffix}" for suffix in checkpoint_suffixes):
            grouped["checkpoint_returns"].add(column)
            continue
        if any(
            column == f"{prefix}_at_{suffix}"
            for suffix in checkpoint_suffixes
            for prefix in ("delta", "gamma", "theta", "iv", "dte")
        ):
            grouped["checkpoint_greeks"].add(column)
            continue
        if any(
            column == f"{prefix}_at_{suffix}"
            for suffix in checkpoint_suffixes
            for prefix in ("time_value_pct", "theta_decay_pct")
        ):
            grouped["checkpoint_time_decay"].add(column)
            continue
        grouped["other"].add(column)

    return {key: sorted(values) for key, values in grouped.items() if values}


def _group_count_map(grouped_columns: dict[str, list[str]]) -> dict[str, int]:
    """Return grouped missing-column counts for lightweight metrics/alerts."""
    return {group: len(columns) for group, columns in grouped_columns.items()}


_WINDOW_FEATURE_COLUMNS: tuple[str, ...] = (
    "window_call_put_imbalance_1h",
    "window_sweep_ratio_1h",
    "window_flow_count_1h",
    "window_call_put_imbalance_1d",
    "window_sweep_ratio_1d",
    "window_dp_volume_1d",
    "window_call_put_ratio_1d",
    "window_call_put_imbalance_1w",
    "window_sweep_ratio_1w",
    "window_call_put_ratio_1w",
)


def _window_feature_select_expressions(available_columns: set[str]) -> str:
    expressions: list[str] = []
    for column in _WINDOW_FEATURE_COLUMNS:
        if column in available_columns:
            expressions.append(f"COALESCE(p.{column}, 0.0) as {column}")
        else:
            expressions.append(f"0.0 as {column}")
    return ",\n            ".join(expressions)


from orion.ml.heber_utils import coerce_dataframe as _coerce_dataframe
from orion.ml.heber_utils import first_existing_column as _first_existing_column
from orion.ml.heber_utils import generate_deterministic_event_ids as _generate_deterministic_event_ids_for_exit


def _normalize_heber_outcomes_for_exit(frame: Any) -> Any:
    import pandas as pd

    if frame.empty:
        return pd.DataFrame(
            columns=[
                "event_id",
                "outcome_return",
                "hit_tp_first",
                "trading_minutes_to_hit",
                "bars_to_hit",
                "snapshot_count",
                "no_snapshot",
            ]
        )

    event_column = _first_existing_column(frame, ["alert_id", "event_id", "watch_id", "instrument_key"])
    outcome_return_column = _first_existing_column(frame, ["outcome_return", "return", "contract_return"])
    hit_tp_column = _first_existing_column(frame, ["hit_tp_first", "contract_hit_tp_first"])
    outcome_column = _first_existing_column(frame, ["outcome", "outcome_reason", "status"])
    trading_minutes_column = _first_existing_column(frame, ["trading_minutes_to_hit", "bars_to_hit"])
    bars_to_hit_column = _first_existing_column(frame, ["bars_to_hit"])
    snapshot_count_column = _first_existing_column(frame, ["snapshot_count"])

    if event_column is None:
        return pd.DataFrame(
            columns=[
                "event_id",
                "outcome_return",
                "hit_tp_first",
                "trading_minutes_to_hit",
                "bars_to_hit",
                "snapshot_count",
                "no_snapshot",
            ]
        )

    normalized = pd.DataFrame({"event_id": frame[event_column].astype(str)})
    normalized["outcome_return"] = pd.to_numeric(
        frame[outcome_return_column] if outcome_return_column else None,
        errors="coerce",
    )

    if hit_tp_column:
        normalized["hit_tp_first"] = pd.to_numeric(frame[hit_tp_column], errors="coerce").fillna(0).astype(int)
    else:
        outcomes = (
            frame[outcome_column].astype(str).str.lower()
            if outcome_column
            else pd.Series(index=frame.index, dtype=object)
        )
        normalized["hit_tp_first"] = outcomes.isin({"hit_tp", "tp", "take_profit"}).astype(int)

    normalized["trading_minutes_to_hit"] = pd.to_numeric(
        frame[trading_minutes_column] if trading_minutes_column else None,
        errors="coerce",
    ).fillna(0.0)
    normalized["bars_to_hit"] = pd.to_numeric(
        frame[bars_to_hit_column] if bars_to_hit_column else None,
        errors="coerce",
    )
    normalized["snapshot_count"] = pd.to_numeric(
        frame[snapshot_count_column] if snapshot_count_column else None,
        errors="coerce",
    )
    outcome_text = (
        frame[outcome_column].astype(str).str.strip().str.lower()
        if outcome_column
        else pd.Series(index=frame.index, dtype=object)
    )
    normalized["no_snapshot"] = outcome_text.str.contains(
        r"no[_\s-]*snapshot|missing[_\s-]*snapshot|insufficient[_\s-]*snapshot|no[_\s-]*data"
    )

    normalized = normalized.dropna(subset=["event_id"])
    normalized["event_id"] = normalized["event_id"].astype(str)
    return normalized


def _drop_no_snapshot_outcomes_for_exit(dataframe: Any) -> tuple[Any, int]:
    import pandas as pd

    if dataframe.empty:
        return dataframe, 0

    drop_mask = pd.Series(False, index=dataframe.index)
    if "no_snapshot" in dataframe.columns:
        drop_mask = drop_mask | dataframe["no_snapshot"].eq(True)

    if "snapshot_count" in dataframe.columns:
        snapshots = pd.to_numeric(dataframe["snapshot_count"], errors="coerce")
        if snapshots.notna().any():
            drop_mask = drop_mask | (snapshots.fillna(0) <= 0)

    filtered = dataframe[~drop_mask].copy()
    dropped = len(dataframe) - len(filtered)
    return filtered, dropped


def _normalize_heber_features_for_exit(frame: Any) -> Any:
    import pandas as pd

    if frame.empty:
        return pd.DataFrame(columns=["event_id"])

    event_column = _first_existing_column(frame, ["alert_id", "event_id", "watch_id", "instrument_key"])
    if event_column is not None:
        normalized = pd.DataFrame({"event_id": frame[event_column].astype(str)})
    else:
        logger.warning(
            "No event_id column in exit features; generating deterministic IDs",
            extra={"event": "exit_classifier_features_synthetic_event_id"},
        )
        normalized = pd.DataFrame({"event_id": _generate_deterministic_event_ids_for_exit(frame)})

    mapped_columns: dict[str, list[str]] = {
        "premium_usd": ["premium_usd", "premium"],
        "dte_at_entry": ["dte_at_entry", "dte", "days_to_expiry"],
        "is_sweep": ["is_sweep"],
        "iv_rank_at_entry": ["iv_rank_at_entry", "iv_rank"],
        "vix_at_entry": ["vix_at_entry"],
        "gex_at_entry": ["gex_at_entry", "gex"],
        "market_tide_30m": ["market_tide_30m"],
        "delta_at_entry": ["delta_at_entry", "delta"],
        "theta_at_entry": ["theta_at_entry", "theta"],
        "iv_at_entry": ["iv_at_entry", "iv"],
        "ask_side_ratio": ["ask_side_ratio"],
        "window_call_put_imbalance_1h": ["window_call_put_imbalance_1h"],
        "window_sweep_ratio_1h": ["window_sweep_ratio_1h"],
        "window_flow_count_1h": ["window_flow_count_1h"],
        "window_call_put_imbalance_1d": ["window_call_put_imbalance_1d"],
        "window_sweep_ratio_1d": ["window_sweep_ratio_1d"],
        "window_dp_volume_1d": ["window_dp_volume_1d"],
        "window_call_put_ratio_1d": ["window_call_put_ratio_1d"],
        "window_call_put_imbalance_1w": ["window_call_put_imbalance_1w"],
        "window_sweep_ratio_1w": ["window_sweep_ratio_1w"],
        "window_call_put_ratio_1w": ["window_call_put_ratio_1w"],
    }

    for target, candidates in mapped_columns.items():
        source = _first_existing_column(frame, candidates)
        normalized[target] = frame[source] if source else 0.0

    normalized["is_sweep"] = normalized["is_sweep"].map(
        lambda value: str(value).strip().lower() in {"1", "true", "t", "yes", "y", "on"}
    )

    ask_ratio_source = _first_existing_column(frame, ["ask_side_ratio"])
    if ask_ratio_source is None:
        ask_ratio = pd.Series(np.nan, index=normalized.index, dtype="float64")
    else:
        ask_ratio = pd.to_numeric(normalized["ask_side_ratio"], errors="coerce")
    side_source = _first_existing_column(frame, ["side", "aggressor"])
    if side_source is not None:
        side_tokens = frame[side_source].astype(str).str.strip().str.lower()

        def _token_to_ask_ratio(token: str) -> float:
            if token in {"ask", "buy", "buyer", "bullish"}:
                return 1.0
            if token in {"bid", "sell", "seller", "bearish"}:
                return 0.0
            if token in {"mid", "midpoint", "neutral"}:
                return 0.5
            return np.nan

        side_ratio = side_tokens.map(_token_to_ask_ratio)
        ask_ratio = ask_ratio.where(ask_ratio.notna(), side_ratio)
    normalized["ask_side_ratio"] = ask_ratio.fillna(0.5)
    return normalized


def _apply_bucket_filter_for_exit(dataframe: Any, bucket: str) -> Any:
    import pandas as pd

    if "dte_at_entry" not in dataframe.columns:
        logger.warning(
            "dte_at_entry column missing from merged exit training data; skipping bucket filter",
            extra={"event": "exit_classifier_missing_dte_at_entry", "bucket": bucket},
        )
        return dataframe

    dte = pd.to_numeric(dataframe["dte_at_entry"], errors="coerce")
    upper_bucket = bucket.upper()

    if upper_bucket == "0DTE":
        return dataframe[dte == 0]
    if upper_bucket == "SHORT_SWING":
        return dataframe[(dte >= 1) & (dte <= 2)]
    if upper_bucket == "SWING":
        return dataframe[(dte >= 3) & (dte <= 14)]
    if upper_bucket == "POSITION":
        return dataframe[dte >= 15]
    return dataframe.iloc[0:0]


async def _build_bucket_training_data_from_heber(
    bucket: str,
    feature_names: list[str],
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    reader = get_heber_reader()
    now = datetime.now(UTC)

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
            f"Failed to read Heber gold datasets for exit training: {exc}",
            extra={"event": "exit_classifier_heber_training_read_failed", "bucket": bucket},
        )
        X_empty, y_empty = _empty_training_arrays(len(feature_names))  # noqa: N806
        return X_empty, y_empty, feature_names

    outcomes = _normalize_heber_outcomes_for_exit(_coerce_dataframe(outcomes_payload))
    features = _normalize_heber_features_for_exit(_coerce_dataframe(features_payload))
    outcomes, dropped_count = _drop_no_snapshot_outcomes_for_exit(outcomes)
    if dropped_count:
        logger.warning(
            f"Dropped {dropped_count} no-snapshot outcomes from exit training set",
            extra={
                "event": "exit_classifier_drop_no_snapshot_outcomes",
                "bucket": bucket,
                "dropped_rows": dropped_count,
                "remaining_rows": len(outcomes),
            },
        )
    if outcomes.empty:
        logger.warning(
            "No Heber outcomes available for exit-classifier training",
            extra={"event": "exit_classifier_heber_training_empty_outcomes", "bucket": bucket},
        )
        X_empty, y_empty = _empty_training_arrays(len(feature_names))  # noqa: N806
        return X_empty, y_empty, feature_names

    merged = outcomes.merge(features, on="event_id", how="left")
    merged = _apply_bucket_filter_for_exit(merged, bucket)

    X_list: list[list[float]] = []  # noqa: N806
    y_list: list[int] = []

    for row in merged.to_dict("records"):
        outcome_return = _safe_float(row.get("outcome_return"), default=float("nan"))
        if isnan(outcome_return):
            continue

        delta_entry = _safe_float(row.get("delta_at_entry"))
        gamma_entry = _safe_float(row.get("gamma_at_entry"))
        theta_entry = _safe_float(row.get("theta_at_entry"))
        iv_entry = _safe_float(row.get("iv_at_entry"))
        dte_entry = int(_safe_float(row.get("dte_at_entry")))

        features_vector = [
            0.0,  # Placeholder: actual current_return populated at inference time
            0.0,  # Placeholder: actual time_held populated at inference time
            delta_entry,
            gamma_entry,
            theta_entry,
            iv_entry,
            float(max(dte_entry, 0)),
            0.0,
            0.0,
            _safe_float(row.get("premium_usd")),
            float(max(dte_entry, 0)),
            1.0 if _is_truthy(row.get("is_sweep")) else 0.0,
            _safe_float(row.get("iv_rank_at_entry"), default=50.0),
            _safe_float(row.get("vix_at_entry"), default=20.0),
            _safe_float(row.get("gex_at_entry")),
            _safe_float(row.get("market_tide_30m")),
            delta_entry,
            theta_entry,
            iv_entry,
            _safe_float(row.get("ask_side_ratio")),
            _safe_float(row.get("window_call_put_imbalance_1h")),
            _safe_float(row.get("window_sweep_ratio_1h")),
            _safe_float(row.get("window_flow_count_1h")),
            _safe_float(row.get("window_call_put_imbalance_1d")),
            _safe_float(row.get("window_sweep_ratio_1d")),
            _safe_float(row.get("window_dp_volume_1d")),
            _safe_float(row.get("window_call_put_ratio_1d")),
            _safe_float(row.get("window_call_put_imbalance_1w")),
            _safe_float(row.get("window_sweep_ratio_1w")),
            _safe_float(row.get("window_call_put_ratio_1w")),
        ]

        if len(features_vector) != len(feature_names):
            continue

        target = 1 if _safe_float(row.get("hit_tp_first"), default=0.0) > 0 else 0
        X_list.append(features_vector)
        y_list.append(target)

    logger.info(
        "Built Heber exit-classifier training data",
        extra={
            "event": "exit_classifier_heber_training_data",
            "bucket": bucket,
            "samples": len(X_list),
        },
    )
    if not X_list:
        X_empty, y_empty = _empty_training_arrays(len(feature_names))  # noqa: N806
        return X_empty, y_empty, feature_names

    return np.array(X_list, dtype=float), np.array(y_list, dtype=int), feature_names


# Bucket-specific checkpoint configurations
# Each checkpoint has: (column_suffix, hours_held, description)
BUCKET_CHECKPOINTS = {
    "0DTE": [
        ("5m", 0.083, "5 minutes"),
        ("10m", 0.167, "10 minutes"),
        ("15m", 0.25, "15 minutes"),
        ("30m", 0.5, "30 minutes"),
        ("1h", 1.0, "1 hour"),
        ("eod", 6.5, "End of day"),  # ~6.5h trading day
    ],
    "SHORT_SWING": [
        ("30m", 0.5, "30 minutes"),
        ("1h", 1.0, "1 hour"),
        ("2h", 2.0, "2 hours"),
        ("4h", 4.0, "4 hours"),
        ("8h", 8.0, "8 hours"),
        ("eod", 6.5, "End of day"),
    ],
    "SWING": [
        ("1h", 1.0, "1 hour"),
        ("4h", 4.0, "4 hours"),
        ("8h", 8.0, "8 hours"),
        ("eod", 6.5, "End of day"),
        ("1d", 24.0, "1 day"),
        ("2d", 48.0, "2 days"),
        ("1w", 168.0, "1 week"),
        ("2w", 336.0, "2 weeks"),
    ],
    "POSITION": [
        ("1d", 24.0, "1 day"),
        ("2d", 48.0, "2 days"),
        ("3d", 72.0, "3 days"),
        ("1w", 168.0, "1 week"),
        ("2w", 336.0, "2 weeks"),
        ("3w", 504.0, "3 weeks"),
        ("4w", 672.0, "4 weeks"),
    ],
}

# Threshold for "good exit" - captured this % of max return
GOOD_EXIT_THRESHOLD = 0.8

# Minimum samples required to train a bucket model
MIN_SAMPLES = 100


@dataclass
class ExitFeatures:
    """Features for exit decision at a checkpoint."""

    # Position state at checkpoint
    current_return_pct: float
    time_held_hours: float
    max_return_so_far: float
    max_drawdown_so_far: float

    # Entry context
    premium_usd: float
    dte_at_entry: int
    is_sweep: bool
    bucket: str = ""

    # Market context at entry
    iv_rank_at_entry: float | None = None
    vix_at_entry: float | None = None
    trend_regime: str | None = None
    vol_regime: str | None = None
    gex_at_entry: float | None = None
    market_tide_30m: float | None = None

    # Entry Greeks
    delta_at_entry: float | None = None
    theta_at_entry: float | None = None
    iv_at_entry: float | None = None
    ask_side_ratio: float | None = None

    # Checkpoint-specific (current position state)
    delta_at_checkpoint: float | None = None
    gamma_at_checkpoint: float | None = None
    theta_at_checkpoint: float | None = None
    iv_at_checkpoint: float | None = None
    dte_at_checkpoint: float | None = None
    time_value_pct: float | None = None
    theta_decay_pct: float | None = None


@dataclass
class ExitPrediction:
    """Exit classifier output."""

    should_exit: bool
    confidence: float
    reasoning: str
    checkpoint: str | None = None


class BucketExitClassifier:
    """
    Bucket-specific exit classifier.

    Each bucket has its own model trained on appropriate time horizons.
    Falls back to heuristic when no model is available.
    """

    def __init__(self) -> None:
        self.models: dict[str, Any] = {}  # bucket -> model_data
        self.feature_names: dict[str, list[str]] = {}
        self._load_models()

    def _infer_bucket(self, features: ExitFeatures) -> str:
        """Infer bucket from entry DTE for backward-compatible callers."""
        bucket = (features.bucket or "").strip().upper()
        if bucket in BUCKET_CHECKPOINTS:
            return bucket

        dte = int(features.dte_at_entry)
        if dte <= 0:
            return "0DTE"
        if dte <= 2:
            return "SHORT_SWING"
        if dte <= 7:
            return "SWING"
        return "POSITION"

    def _load_models(self) -> None:
        """Load all bucket-specific exit models."""
        if not MODEL_DIR.exists():
            logger.info("Model directory does not exist, using heuristic")
            return

        loaded_count = 0
        for bucket in BUCKET_CHECKPOINTS:
            model_path = MODEL_DIR / f"{bucket}_exit.pkl"
            if model_path.exists():
                try:
                    with open(model_path, "rb") as f:
                        model_data = pickle.load(f)
                    self.models[bucket] = model_data
                    self.feature_names[bucket] = model_data.get("feature_names", [])
                    loaded_count += 1
                    logger.info(
                        f"Loaded exit model: {bucket}",
                        extra={"event": "exit_model_loaded", "bucket": bucket},
                    )
                except Exception as e:
                    logger.warning(f"Failed to load exit model {bucket}: {e}")

        if loaded_count == 0:
            logger.info(
                "No exit models found, using heuristic classifiers",
                extra={"event": "using_exit_heuristic"},
            )
        else:
            logger.info(
                f"Loaded {loaded_count}/{len(BUCKET_CHECKPOINTS)} exit models",
                extra={"event": "exit_models_loaded", "count": loaded_count},
            )

    def predict(self, features: ExitFeatures) -> ExitPrediction:
        """
        Predict whether to exit given current position state.

        Uses bucket-specific model if available, else heuristic.
        """
        bucket = self._infer_bucket(features)
        features.bucket = bucket

        if bucket in self.models:
            return self._ml_predict(features, bucket)
        else:
            return self._heuristic_predict(features)

    def _ml_predict(self, features: ExitFeatures, bucket: str) -> ExitPrediction:
        """ML-based prediction."""
        model_data = self.models[bucket]
        model = model_data.get("model")

        if model is None:
            return self._heuristic_predict(features)

        try:
            feature_names = self.feature_names[bucket]
            feature_dict = self._features_to_dict(features)
            feature_vector = np.array([[feature_dict.get(f, 0) for f in feature_names]])

            prob = model.predict_proba(feature_vector)[0][1]
            should_exit = prob >= 0.5

            return ExitPrediction(
                should_exit=should_exit,
                confidence=float(prob),
                reasoning=f"ML exit score: {prob:.2f}",
            )
        except Exception as e:
            logger.warning(f"ML exit prediction failed: {e}")
            return self._heuristic_predict(features)

    def _features_to_dict(self, features: ExitFeatures) -> dict[str, float]:
        """Convert ExitFeatures to dict for ML model."""
        return {
            # Position state at checkpoint
            "current_return_pct": features.current_return_pct,
            "time_held_hours": features.time_held_hours,
            # Greeks evolution at checkpoint
            "delta_at_checkpoint": float(features.delta_at_checkpoint or 0),
            "gamma_at_checkpoint": float(features.gamma_at_checkpoint or 0),
            "theta_at_checkpoint": float(features.theta_at_checkpoint or 0),
            "iv_at_checkpoint": float(features.iv_at_checkpoint or 0),
            "dte_at_checkpoint": float(features.dte_at_checkpoint or 0),
            # Time value decay
            "time_value_pct": float(features.time_value_pct or 0),
            "theta_decay_pct": float(features.theta_decay_pct or 0),
            # Entry context
            "premium_usd": features.premium_usd,
            "dte_at_entry": float(features.dte_at_entry),
            "is_sweep": 1.0 if features.is_sweep else 0.0,
            "iv_rank_at_entry": float(features.iv_rank_at_entry or 50),
            "vix_at_entry": float(features.vix_at_entry or 20),
            "gex_at_entry": float(features.gex_at_entry or 0),
            "market_tide_30m": float(features.market_tide_30m or 0),
            # Entry Greeks
            "delta_at_entry": float(features.delta_at_entry or 0),
            "theta_at_entry": float(features.theta_at_entry or 0),
            "iv_at_entry": float(features.iv_at_entry or 0),
            "ask_side_ratio": float(features.ask_side_ratio or 0),
        }

    def _heuristic_predict(self, features: ExitFeatures) -> ExitPrediction:
        """
        Bucket-aware heuristic exit logic.

        Different buckets have different time and return sensitivities.
        """
        reasons = []
        exit_score = 0.0
        bucket = features.bucket

        # Get bucket-specific thresholds
        thresholds = self._get_bucket_thresholds(bucket)

        # Profit target hit
        if features.current_return_pct >= thresholds["take_profit"]:
            exit_score += 0.5
            reasons.append(f"Return hit {features.current_return_pct:.0f}% (TP={thresholds['take_profit']}%)")
        elif features.current_return_pct >= thresholds["partial_profit"]:
            exit_score += 0.2
            reasons.append(f"Solid {features.current_return_pct:.0f}% gain")

        # Stop loss
        if features.current_return_pct <= -thresholds["stop_loss"]:
            exit_score += 0.5
            reasons.append(f"Stop loss at {features.current_return_pct:.0f}%")

        # Time-based urgency (bucket-specific)
        time_urgency = self._calculate_time_urgency(features, thresholds)
        if time_urgency > 0:
            exit_score += time_urgency
            reasons.append(f"Time urgency ({bucket})")

        # Momentum reversal
        if features.max_return_so_far > 10 and features.current_return_pct < 0:
            exit_score += 0.4
            reasons.append("Momentum reversal")

        # Drawdown from peak
        drawdown_from_peak = features.max_return_so_far - features.current_return_pct
        if drawdown_from_peak > thresholds["trailing_stop"] and features.max_return_so_far > 20:
            exit_score += 0.3
            reasons.append(f"Trailing stop: gave back {drawdown_from_peak:.0f}%")

        should_exit = exit_score >= 0.5

        return ExitPrediction(
            should_exit=should_exit,
            confidence=min(exit_score, 1.0),
            reasoning="; ".join(reasons) if reasons else "No exit signal",
        )

    def _get_bucket_thresholds(self, bucket: str) -> dict[str, float]:
        """Get bucket-specific exit thresholds."""
        # 0DTE: Quick profits, tight stops (high theta)
        # POSITION: Let winners run, wider stops
        thresholds = {
            "0DTE": {
                "take_profit": 30,  # Quick 30% wins
                "partial_profit": 15,
                "stop_loss": 15,  # Tight 15% stop
                "trailing_stop": 10,
                "max_hold_hours": 4,  # Exit before EOD
            },
            "SHORT_SWING": {
                "take_profit": 40,
                "partial_profit": 20,
                "stop_loss": 20,
                "trailing_stop": 15,
                "max_hold_hours": 16,  # 2 trading days
            },
            "SWING": {
                "take_profit": 50,
                "partial_profit": 25,
                "stop_loss": 20,
                "trailing_stop": 15,
                "max_hold_hours": 48,  # ~3 trading days
            },
            "POSITION": {
                "take_profit": 75,  # Let runners run
                "partial_profit": 40,
                "stop_loss": 25,  # Wider stop
                "trailing_stop": 20,
                "max_hold_hours": 168,  # 1 week
            },
        }
        return thresholds.get(bucket, thresholds["SWING"])

    def _calculate_time_urgency(self, features: ExitFeatures, thresholds: dict) -> float:
        """Calculate time-based exit urgency."""
        max_hold = thresholds["max_hold_hours"]
        time_pct = features.time_held_hours / max_hold

        # 0DTE has high theta decay at end of day
        if features.bucket == "0DTE":
            if time_pct >= 0.8:  # Last 20% of allowed hold time
                return 0.5
            elif time_pct >= 0.6:
                return 0.3
        else:
            # Other buckets: gentle time pressure
            if time_pct >= 0.9:
                return 0.3
            elif time_pct >= 0.75:
                return 0.1

        return 0.0

    def predict_batch(self, features_list: list[ExitFeatures]) -> list[ExitPrediction]:
        """Predict exit for multiple positions."""
        return [self.predict(f) for f in features_list]


async def build_bucket_training_data(
    bucket: str,
    force_schema_refresh: bool = False,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """
    Build training dataset for a specific bucket.

    Uses bucket-appropriate checkpoints from the configured training source.
    """
    feature_names = list(EXIT_FEATURE_NAMES)
    enabled, control_key, control_raw = _legacy_exit_training_control()
    if not enabled:
        logger.warning(
            "Legacy exit-classifier training disabled by config",
            extra={
                "event": "legacy_label_pipeline_disabled",
                "pipeline": "orion.ml.exit_classifier",
                "control_key": control_key,
                "control_raw": control_raw,
            },
        )
        X_empty, y_empty = _empty_training_arrays(len(feature_names))  # noqa: N806
        return X_empty, y_empty, feature_names

    checkpoints = BUCKET_CHECKPOINTS.get(bucket, [])
    if not checkpoints:
        logger.warning(f"No checkpoints defined for bucket {bucket}")
        X_empty, y_empty = _empty_training_arrays(len(feature_names))  # noqa: N806
        return X_empty, y_empty, feature_names

    _ = force_schema_refresh
    _ = _exit_classifier_training_source()
    return await _build_bucket_training_data_from_heber(bucket, feature_names)


async def train_bucket_exit_classifier(
    bucket: str,
    force_schema_refresh: bool = False,
) -> dict[str, Any] | None:
    """Train exit classifier for a specific bucket."""
    X, y, feature_names = await build_bucket_training_data(  # noqa: N806
        bucket,
        force_schema_refresh=force_schema_refresh,
    )

    can_train, reason = _can_train_with_labels(y, min_samples=MIN_SAMPLES)
    if not can_train:
        logger.warning(
            f"Skipping exit classifier training for {bucket}: {reason}",
            extra={"bucket": bucket, "samples": len(X), "reason": reason},
        )
        return None

    try:
        from lightgbm import LGBMClassifier
    except ImportError:
        logger.error("LightGBM not installed")
        return None

    # Check for class imbalance
    positive_rate = np.mean(y)
    if positive_rate < 0.05 or positive_rate > 0.95:
        logger.warning(
            f"Extreme class imbalance for {bucket}: {positive_rate:.2%} positive",
            extra={"bucket": bucket, "positive_rate": positive_rate},
        )

    # Split data
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)  # noqa: N806

    # Train model
    model = LGBMClassifier(
        n_estimators=100,
        max_depth=5,
        learning_rate=0.1,
        random_state=42,
        class_weight="balanced",  # Handle imbalance
    )

    model.fit(X_train, y_train)

    # Evaluate
    y_pred_proba = model.predict_proba(X_test)[:, 1]
    try:
        auc = roc_auc_score(y_test, y_pred_proba)
    except ValueError:
        auc = 0.5  # Single class in test set

    logger.info(
        f"Exit classifier trained for {bucket}: AUC={auc:.3f}",
        extra={"event": "exit_model_trained", "bucket": bucket, "auc": auc, "samples": len(X)},
    )

    # Save model
    model_data = {
        "model": model,
        "feature_names": feature_names,
        "auc": auc,
        "sample_size": len(X),
        "bucket": bucket,
    }

    if auc >= 0.55:  # Only save if better than random
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        model_path = MODEL_DIR / f"{bucket}_exit.pkl"
        with open(model_path, "wb") as f:
            pickle.dump(model_data, f)
        logger.info(f"Saved exit model to {model_path}")
    else:
        logger.warning(f"Exit model AUC too low ({auc:.3f}), not saving {bucket}")

    return model_data


async def train_all_exit_classifiers(
    force_schema_refresh: bool = False,
    refresh_each_bucket: bool = False,
) -> dict[str, Any]:
    """Train exit classifiers for all buckets."""
    if force_schema_refresh and not refresh_each_bucket:
        refreshed_columns = await _load_price_target_label_columns(force_refresh=True)
        logger.info(
            "Forced schema refresh before all-bucket exit training",
            extra={
                "event": "exit_training_schema_forced_refresh",
                "column_count": len(refreshed_columns),
                "refresh_strategy": "prefetch_once",
            },
        )

    results = {}
    for bucket in BUCKET_CHECKPOINTS:
        logger.info(f"Training exit classifier for {bucket}...")
        result = await train_bucket_exit_classifier(
            bucket,
            force_schema_refresh=(force_schema_refresh and refresh_each_bucket),
        )
        if result:
            results[bucket] = {
                "auc": result.get("auc"),
                "samples": result.get("sample_size"),
            }

    logger.info(
        f"Exit classifier training complete: {len(results)}/{len(BUCKET_CHECKPOINTS)} models trained",
        extra={"event": "all_exit_training_complete", "results": results},
    )
    return results


# Singleton
_exit_classifier: BucketExitClassifier | None = None


def get_exit_classifier() -> BucketExitClassifier:
    """Get or create exit classifier singleton."""
    global _exit_classifier
    if _exit_classifier is None:
        _exit_classifier = BucketExitClassifier()
    return _exit_classifier


def reload_exit_classifier() -> BucketExitClassifier:
    """Force reload exit classifiers (after training)."""
    global _exit_classifier
    _exit_classifier = BucketExitClassifier()
    return _exit_classifier


class ExitClassifier(BucketExitClassifier):
    """
    Backward-compatible alias for legacy tests/callers.

    Historical API expected `use_heuristic` and `model` attributes from a single-model
    classifier; bucketed classifier keeps those semantics via compatibility fields.
    """

    def __init__(self) -> None:
        super().__init__()
        self.use_heuristic = len(self.models) == 0
        self.model = None
