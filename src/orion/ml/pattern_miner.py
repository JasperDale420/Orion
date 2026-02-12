"""
ML Pattern Miner.

Core logic for training LightGBM models on trading data and extracting
human-readable rules for the EOD agent.
"""

import hashlib
import os
import pickle
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy import text

from orion.config import SystemSettings, system_settings
from orion.ml.schemas import (
    FeatureImportance,
    MLInsightsSummary,
    PatternInsight,
    TreeRule,
)
from orion.shared.db_utils import db_query, db_write
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.ml.pattern_miner")

# Model output directory - models are saved here for MLScorer to load
MODEL_DIR = system_settings.model_dir


# Feature configuration - ENTRY-TIME ONLY (no outcome leakage)
# These features are known at trade entry and don't reveal the outcome
FEATURE_COLUMNS = [
    # Entry context (from price_target_labels)
    "iv_rank_at_entry",
    "gex_at_entry",
    "vex_at_entry",
    "market_tide_30m",
    "max_pain_distance_pct",
    "premium_usd",
    "dte",
    "vix_at_entry",
    # Darkpool features (at entry time)
    "darkpool_volume_1h",
    "darkpool_30m",
    "darkpool_4h",
    "darkpool_1d",
    # Options Greeks at entry (critical for options trading)
    "delta_at_entry",
    "gamma_at_entry",
    "theta_at_entry",
    "vega_at_entry",
    "iv_at_entry",
    "iv_vs_hv_ratio",
    # Volume and OI
    "volume_at_entry",
    "open_interest_at_entry",
    "rvol_1h",
    "rvol_daily",
    "oi_change_1d",
    "oi_change_pct",
    # Flow signals
    "ask_side_ratio",
    "sweep_ratio_1h",
    "same_ticker_premium_1h",
    "sector_net_premium_1h",
    # Market context
    "spy_correlation_5d",
    "spy_return_1h",
    "vwap_distance_pct",
    "high_52w_distance_pct",
    "overnight_gap_pct",
    # Timing
    "entry_hour",
    "minutes_to_close",
    # Earnings/Events
    "days_to_earnings",
    # NOTE: Removed outcome features that cause data leakage:
    # - max_return_pct, max_drawdown_pct, time_to_max_seconds
    # - holding_period_seconds, return_at_1h/2h/4h, first_exit_type
    # - opposing_flow_count (during holding period, not at entry)
]

CATEGORICAL_COLUMNS = [
    "put_call",
    "aggressor",
    "is_sweep",
    "is_spread_leg",
    "is_post_earnings",
    "earnings_in_dte_window",
    "entry_session",
    "entry_day_of_week",
    "sector",
    "industry",
    # Regimes
    "vol_regime_at_entry",
    "risk_regime_at_entry",
    "session_regime_at_entry",
    "trend_regime_at_entry",
    "vix_regime_at_entry",
    "market_tide_direction",
    "sector_flow_direction",
]

# Target definitions - 4 targets for diverse signal dimensions
# Note: quick_winner has bucket-specific thresholds defined in TRADE_BUCKET_CONFIGS
TARGETS = {
    # Original targets
    "hit_target_50": """
        CASE WHEN hit_50_pct_ts IS NOT NULL
             AND (hit_stop_20_pct_ts IS NULL OR hit_50_pct_ts < hit_stop_20_pct_ts)
        THEN 1 ELSE 0 END
    """,
    "avoid_stop": """
        CASE WHEN hit_stop_20_pct_ts IS NULL THEN 1 ELSE 0 END
    """,
    # New targets
    "hit_target_100": """
        CASE WHEN hit_100_pct_ts IS NOT NULL
             AND (hit_stop_20_pct_ts IS NULL OR hit_100_pct_ts < hit_stop_20_pct_ts)
        THEN 1 ELSE 0 END
    """,
    # quick_winner is dynamically generated per bucket - see get_quick_winner_target()
}


def _legacy_pattern_training_control() -> tuple[bool, str, str]:
    settings = SystemSettings()

    specific_key = "ORION_ENABLE_LEGACY_PATTERN_MINER_TRAINING"
    if settings.legacy_pattern_miner_training_enabled is not None:
        enabled = settings.legacy_pattern_miner_training_enabled
        raw = "true" if enabled else "false"
        return enabled, specific_key, raw

    global_key = "ORION_ENABLE_LEGACY_LABEL_PIPELINES"
    enabled = settings.legacy_label_pipelines_enabled
    raw = "true" if enabled else "false"
    return enabled, global_key, raw


def _legacy_pattern_training_enabled() -> bool:
    enabled, _, _ = _legacy_pattern_training_control()
    return enabled


def get_quick_winner_target(seconds_threshold: int) -> str:
    """Generate quick_winner target SQL with bucket-specific time threshold."""
    return f"""
        CASE WHEN hit_50_pct_ts IS NOT NULL
             AND time_to_50_pct_seconds IS NOT NULL
             AND time_to_50_pct_seconds < {seconds_threshold}
             AND (hit_stop_20_pct_ts IS NULL OR hit_50_pct_ts < hit_stop_20_pct_ts)
        THEN 1 ELSE 0 END
    """


def _exit_classifier_schema_refresh_config_from_env() -> tuple[bool, bool]:
    """Read schema-refresh strategy flags for exit-classifier training orchestration."""
    force_schema_refresh, refresh_each_bucket, _source = _exit_classifier_schema_refresh_config_details_from_env()
    return force_schema_refresh, refresh_each_bucket


def _exit_classifier_schema_refresh_config_details_from_env() -> tuple[bool, bool, str]:
    """Read schema-refresh config with source metadata for observability."""
    strategy = (
        os.getenv(
            "ORION_EXIT_CLASSIFIER_SCHEMA_REFRESH_STRATEGY",
            "",
        )
        .strip()
        .lower()
    )
    if strategy:
        if strategy in {"off", "disabled", "none", "false"}:
            return False, False, "strategy_env"
        if strategy in {"prefetch_once", "once"}:
            return True, False, "strategy_env"
        if strategy in {"per_bucket", "each_bucket", "each"}:
            return True, True, "strategy_env"
        logger.warning(
            "Invalid ORION_EXIT_CLASSIFIER_SCHEMA_REFRESH_STRATEGY; falling back to legacy flags",
            extra={
                "event": "exit_training_schema_refresh_strategy_invalid",
                "strategy": strategy,
            },
        )

    force_schema_refresh = os.getenv(
        "ORION_EXIT_CLASSIFIER_FORCE_SCHEMA_REFRESH",
        "false",
    ).strip().lower() in {"1", "true", "yes", "y", "on"}
    refresh_each_bucket = os.getenv(
        "ORION_EXIT_CLASSIFIER_REFRESH_EACH_BUCKET",
        "false",
    ).strip().lower() in {"1", "true", "yes", "y", "on"}

    if refresh_each_bucket and not force_schema_refresh:
        logger.warning(
            "Ignoring ORION_EXIT_CLASSIFIER_REFRESH_EACH_BUCKET because force refresh is disabled",
            extra={
                "event": "exit_training_schema_refresh_config_invalid",
                "force_schema_refresh": force_schema_refresh,
                "refresh_each_bucket": refresh_each_bucket,
            },
        )
        refresh_each_bucket = False

    source = "strategy_env_invalid_fallback" if strategy else "legacy_flags"
    return force_schema_refresh, refresh_each_bucket, source


def _exit_classifier_schema_refresh_mode(force_schema_refresh: bool, refresh_each_bucket: bool) -> str:
    """Return a human-readable refresh strategy mode label."""
    if not force_schema_refresh:
        return "off"
    if refresh_each_bucket:
        return "per_bucket"
    return "prefetch_once"


# Trade bucket configurations with bucket-specific lookback windows
TRADE_BUCKET_CONFIGS = {
    "0DTE": {
        "filter": "trade_type = '0DTE'",
        "window_days": 10,  # Short lookback - fast-changing dynamics
        "min_samples": 50,
        "quick_winner_seconds": 3600,  # 1 hour - fast moves expected
        "description": "Same-day expiry options",
    },
    "SHORT_SWING": {
        "filter": "trade_type = 'SHORT_SWING'",
        "window_days": 20,  # 1-3 DTE trades
        "min_samples": 50,
        "quick_winner_seconds": 14400,  # 4 hours
        "description": "1-3 day expiry options",
    },
    "SWING": {
        "filter": "trade_type = 'SWING'",
        "window_days": 45,  # 3-14 DTE trades
        "min_samples": 30,
        "quick_winner_seconds": 86400,  # 1 day
        "description": "3-14 day expiry options",
    },
    "POSITION": {
        "filter": "trade_type = 'POSITION'",
        "window_days": 90,  # Long-term trades need more history
        "min_samples": 20,
        "quick_winner_seconds": 259200,  # 3 days
        "description": "14+ day expiry options",
    },
}


async def fetch_training_data(
    window_days: int = 30,
    min_samples: int = 100,
    trade_type_filter: Optional[str] = None,
    quick_winner_seconds: int = 3600,
) -> Tuple[Any, List[str]]:
    """
    Fetch training data from price_target_labels.

    Args:
        window_days: Number of days to look back
        min_samples: Minimum samples required
        trade_type_filter: Optional SQL filter for trade_type (e.g., "trade_type = '0DTE'")
        quick_winner_seconds: Time threshold for quick_winner target (bucket-specific)

    Returns:
        Tuple of (pandas DataFrame, list of feature names)
    """
    import pandas as pd

    enabled, control_key, control_raw = _legacy_pattern_training_control()
    if not enabled:
        logger.warning(
            "Legacy pattern-miner training disabled by config",
            extra={
                "event": "legacy_label_pipeline_disabled",
                "pipeline": "orion.ml.pattern_miner",
                "control_key": control_key,
                "control_raw": control_raw,
            },
        )
        return None, []

    feature_cols = ", ".join(FEATURE_COLUMNS + CATEGORICAL_COLUMNS)

    # Build target columns - include dynamic quick_winner
    all_targets = dict(TARGETS)
    all_targets["quick_winner"] = get_quick_winner_target(quick_winner_seconds)
    target_cols = ", ".join([f"({sql}) as target_{name}" for name, sql in all_targets.items()])

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    # Build WHERE clause with optional trade_type filter
    where_clause = "entry_ts >= :cutoff AND last_tracked_ts IS NOT NULL"
    if trade_type_filter:
        where_clause += f" AND {trade_type_filter}"

    async def query(session: Any) -> List[Any]:
        stmt = text(
            f"""
            SELECT
                event_id,
                entry_ts,
                {feature_cols},
                {target_cols}
            FROM price_target_labels
            WHERE {where_clause}
            ORDER BY entry_ts ASC
        """
        )
        result = await session.execute(stmt, {"cutoff": cutoff})
        return result.fetchall()

    rows = await db_query(query)

    if len(rows) < min_samples:
        logger.warning(f"Insufficient samples: {len(rows)} < {min_samples}")
        return None, []

    # Column names from query
    columns = (
        ["event_id", "entry_ts"]
        + FEATURE_COLUMNS
        + CATEGORICAL_COLUMNS
        + [f"target_{name}" for name in all_targets.keys()]
    )

    df = pd.DataFrame(rows, columns=columns)

    filter_desc = f" (filter: {trade_type_filter})" if trade_type_filter else ""
    logger.info(
        f"Fetched {len(df)} training samples from last {window_days} days{filter_desc}",
        extra={"event": "ml_data_fetch", "sample_count": len(df), "window_days": window_days},
    )

    return df, FEATURE_COLUMNS + CATEGORICAL_COLUMNS


def prepare_features(df: Any, feature_names: List[str]) -> Tuple[Any, Any]:
    """
    Prepare feature matrix X from dataframe.

    Returns:
        Tuple of (X feature matrix, feature_names used)
    """
    import pandas as pd

    X = df[feature_names].copy()

    # Encode categoricals
    for col in CATEGORICAL_COLUMNS:
        if col in X.columns:
            X[col] = pd.Categorical(X[col]).codes

    # Fill missing with -999 (LightGBM handles this)
    X = X.fillna(-999)

    return X, feature_names


def train_model(
    X: Any,
    y: Any,
    test_size: float = 0.2,
    use_walk_forward: bool = True,
    n_splits: int = 5,
    dates: Any = None,
) -> Tuple[Any, float, float]:
    """
    Train LightGBM classifier using walk-forward or random validation.

    Args:
        X: Feature matrix
        y: Target labels
        test_size: Holdout size (only used if walk_forward=False)
        use_walk_forward: If True, use time-series walk-forward CV
        n_splits: Number of walk-forward folds
        dates: Timestamps for ordering (required if use_walk_forward=True)

    Returns:
        Tuple of (model, train_auc, holdout_auc)
    """

    # LightGBM parameters - fast and interpretable
    params = {
        "objective": "binary",
        "metric": "auc",
        "boosting_type": "gbdt",
        "num_leaves": 16,  # Keep trees shallow for interpretability
        "max_depth": 4,
        "learning_rate": 0.1,
        "n_estimators": 100,
        "min_child_samples": 20,
        "verbose": -1,
    }

    if use_walk_forward and dates is not None:
        # Walk-forward (expanding window) validation
        return _train_walk_forward(X, y, dates, params, n_splits)
    else:
        # Fallback to random split (legacy behavior)
        return _train_random_split(X, y, params, test_size)


def _train_random_split(X: Any, y: Any, params: dict, test_size: float) -> Tuple[Any, float, float]:
    """Train with random train/test split (legacy behavior)."""
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import train_test_split

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=test_size, random_state=42, stratify=y)

    model = lgb.LGBMClassifier(**params)
    model.fit(X_train, y_train)

    train_pred = model.predict_proba(X_train)[:, 1]
    test_pred = model.predict_proba(X_test)[:, 1]

    train_auc = roc_auc_score(y_train, train_pred)
    holdout_auc = roc_auc_score(y_test, test_pred)

    logger.info(
        f"Model trained (random split): train_auc={train_auc:.3f}, holdout_auc={holdout_auc:.3f}",
        extra={"event": "ml_model_train", "method": "random_split", "train_auc": train_auc, "holdout_auc": holdout_auc},
    )

    return model, train_auc, holdout_auc


def _train_walk_forward(X: Any, y: Any, dates: Any, params: dict, n_splits: int = 5) -> Tuple[Any, float, float]:
    """
    Train with walk-forward (expanding window) validation.

    This prevents look-ahead bias by always training on past data
    and testing on future data.
    """
    import lightgbm as lgb
    import numpy as np
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import TimeSeriesSplit

    # Sort by date to ensure temporal ordering
    sort_idx = np.argsort(dates)
    x_sorted = X.iloc[sort_idx] if hasattr(X, "iloc") else X[sort_idx]
    y_sorted = y.iloc[sort_idx] if hasattr(y, "iloc") else y[sort_idx]

    tscv = TimeSeriesSplit(n_splits=n_splits)

    fold_aucs = []

    for fold, (train_idx, test_idx) in enumerate(tscv.split(x_sorted)):
        x_train, x_test = x_sorted.iloc[train_idx], x_sorted.iloc[test_idx]
        y_train, y_test = y_sorted.iloc[train_idx], y_sorted.iloc[test_idx]

        # Skip if either class is missing
        if len(np.unique(y_train)) < 2 or len(np.unique(y_test)) < 2:
            logger.warning(f"Fold {fold + 1}: Skipped due to single class")
            continue

        model = lgb.LGBMClassifier(**params)
        model.fit(x_train, y_train)

        test_pred = model.predict_proba(x_test)[:, 1]
        fold_auc = roc_auc_score(y_test, test_pred)
        fold_aucs.append(fold_auc)

        logger.debug(f"Fold {fold + 1}/{n_splits}: AUC={fold_auc:.3f}")

    if not fold_aucs:
        logger.warning("Walk-forward CV failed: no valid folds")
        return _train_random_split(X, y, params, 0.2)

    avg_auc = np.mean(fold_aucs)
    std_auc = np.std(fold_aucs)

    # Final model: train on all data (for production use)
    final_model = lgb.LGBMClassifier(**params)
    final_model.fit(x_sorted, y_sorted)

    # Use average CV AUC as holdout estimate
    train_pred = final_model.predict_proba(x_sorted)[:, 1]
    train_auc = roc_auc_score(y_sorted, train_pred)

    logger.info(
        f"Model trained (walk-forward): cv_auc={avg_auc:.3f}±{std_auc:.3f}, train_auc={train_auc:.3f}",
        extra={
            "event": "ml_model_train",
            "method": "walk_forward",
            "n_splits": n_splits,
            "cv_auc": avg_auc,
            "cv_std": std_auc,
            "train_auc": train_auc,
        },
    )

    # Return CV AUC as holdout estimate (more realistic than full-data train AUC)
    return final_model, train_auc, avg_auc


def save_model(model: Any, model_type: str, feature_names: List[str]) -> Optional[Path]:
    """
    Save trained model to disk for MLScorer to load.

    Args:
        model: Trained LightGBM model
        model_type: Model identifier (e.g., "SWING_hit_target_50")
        feature_names: List of feature names used for training

    Returns:
        Path to saved model, or None if save failed
    """
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    model_path = MODEL_DIR / f"{model_type}.pkl"

    try:
        # Save model with metadata
        model_data = {
            "model": model,
            "feature_names": feature_names,
            "model_type": model_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(model_path, "wb") as f:
            pickle.dump(model_data, f)

        logger.info(
            f"Saved model to {model_path}",
            extra={"event": "model_saved", "model_type": model_type, "path": str(model_path)},
        )
        return model_path
    except Exception as e:
        logger.error(f"Failed to save model {model_type}: {e}")
        return None


def extract_feature_importance(
    model: Any,
    feature_names: List[str],
    top_k: int = 10,
) -> List[FeatureImportance]:
    """
    Extract top feature importances from trained model.
    """
    importances = model.feature_importances_
    indices = np.argsort(importances)[::-1][:top_k]

    results = []
    for rank, idx in enumerate(indices, 1):
        results.append(
            FeatureImportance(
                feature=feature_names[idx],
                importance=float(importances[idx]),
                rank=rank,
            )
        )

    return results


def extract_tree_rules(
    model: Any,
    feature_names: List[str],
    X: Any,
    y: Any,
    top_k: int = 5,
) -> List[TreeRule]:
    """
    Extract human-readable rules from decision tree splits.

    Uses the first tree and extracts the most predictive leaf nodes.
    """
    import pandas as pd

    rules = []

    # Get leaf assignments for each sample
    leaf_indices = model.predict(X, pred_leaf=True)

    if len(leaf_indices.shape) > 1:
        # Use first tree only for interpretability
        leaf_indices = leaf_indices[:, 0]

    # Calculate hit rate per leaf
    df_leaves = pd.DataFrame({"leaf": leaf_indices, "target": y.values})
    leaf_stats = (
        df_leaves.groupby("leaf")
        .agg(
            hit_rate=("target", "mean"),
            sample_size=("target", "count"),
        )
        .reset_index()
    )

    # Filter to leaves with enough samples
    leaf_stats = leaf_stats[leaf_stats["sample_size"] >= 10]

    # Sort by hit rate deviation from base rate (most informative)
    base_rate = y.mean()
    leaf_stats["deviation"] = abs(leaf_stats["hit_rate"] - base_rate)
    leaf_stats = leaf_stats.sort_values("deviation", ascending=False).head(top_k)

    # Generate rule descriptions (simplified - would need tree structure for exact rules)
    for _, row in leaf_stats.iterrows():
        # Get samples in this leaf
        mask = leaf_indices == row["leaf"]
        leaf_X = X[mask]

        # Find distinguishing features (simplified heuristic)
        conditions = []
        for feat in feature_names[:3]:  # Top 3 features
            if feat in leaf_X.columns:
                mean_val = leaf_X[feat].mean()
                overall_mean = X[feat].mean()
                if mean_val > overall_mean * 1.2:
                    conditions.append(f"{feat} > avg")
                elif mean_val < overall_mean * 0.8:
                    conditions.append(f"{feat} < avg")

        condition_str = " AND ".join(conditions) if conditions else f"Leaf {row['leaf']}"

        rules.append(
            TreeRule(
                condition=condition_str,
                hit_rate=float(row["hit_rate"]),
                sample_size=int(row["sample_size"]),
                confidence=min(1.0, row["sample_size"] / 50),  # Simple confidence metric
            )
        )

    return rules


async def get_last_week_importance(model_type: str) -> Dict[str, float]:
    """
    Fetch last week's feature importance for drift detection.
    """

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    async def query(session: Any) -> Dict[str, float]:
        stmt = text(
            """
            SELECT feature_name, importance
            FROM ml_feature_importance_history
            WHERE model_type = :model_type
            AND created_at_utc >= :cutoff
            ORDER BY created_at_utc DESC
        """
        )
        result = await session.execute(stmt, {"model_type": model_type, "cutoff": cutoff})
        rows = result.fetchall()
        return {row[0]: row[1] for row in rows}

    return await db_query(query)


async def persist_insight(insight: PatternInsight) -> None:
    """
    Persist pattern insight to database.
    """
    from orion.storage.models_ml import MLFeatureImportanceHistory, MLPatternInsight

    async def write(session: Any) -> None:
        # Save main insight
        session.add(
            MLPatternInsight(
                insight_id=insight.insight_id,
                model_type=insight.model_type,
                training_window_days=insight.training_window_days,
                sample_size=insight.sample_size,
                positive_rate=insight.positive_rate,
                train_auc=insight.train_auc,
                holdout_auc=insight.holdout_auc,
                top_rules_json=[r.model_dump() for r in insight.top_rules],
                top_features_json=[f.model_dump() for f in insight.top_features],
                degraded_features_json=insight.degraded_features,
                emerging_patterns_json=insight.emerging_patterns,
                metadata_json=insight.metadata,
            )
        )

        # Save feature importance history
        for feat in insight.top_features:
            session.add(
                MLFeatureImportanceHistory(
                    id=str(uuid.uuid4()),
                    model_type=insight.model_type,
                    feature_name=feat.feature,
                    importance=feat.importance,
                    rank=feat.rank,
                )
            )

    await db_write(write)


async def run_pattern_mining(
    target_name: str = "hit_target_50",
    bucket_name: Optional[str] = None,
    bucket_config: Optional[Dict[str, Any]] = None,
) -> Optional[PatternInsight]:
    """
    Run full pattern mining pipeline for a single target and optional bucket.

    Args:
        target_name: Target to predict (hit_target_50, avoid_stop)
        bucket_name: Trade bucket name (0DTE, SHORT_SWING, SWING, POSITION)
        bucket_config: Config dict with filter, window_days, min_samples

    Returns:
        PatternInsight if successful, None otherwise.
    """
    # Determine model_type name (includes bucket if specified)
    model_type = f"{bucket_name}_{target_name}" if bucket_name else target_name

    # Get config values
    window_days = bucket_config.get("window_days", 30) if bucket_config else 30
    min_samples = bucket_config.get("min_samples", 50) if bucket_config else 50
    trade_filter = bucket_config.get("filter") if bucket_config else None
    quick_winner_seconds = bucket_config.get("quick_winner_seconds", 3600) if bucket_config else 3600

    logger.info(
        f"Starting pattern mining for {model_type}",
        extra={"bucket": bucket_name, "target": target_name, "window_days": window_days},
    )

    # 1. Fetch data (filtered by bucket)
    df, feature_names = await fetch_training_data(
        window_days=window_days,
        min_samples=min_samples,
        trade_type_filter=trade_filter,
        quick_winner_seconds=quick_winner_seconds,
    )
    if df is None or df.empty:
        logger.warning(f"No training data available for {model_type}")
        return None

    # 2. Prepare features
    X, feature_names = prepare_features(df, feature_names)
    y = df[f"target_{target_name}"]

    # Check for valid target distribution
    if y.nunique() < 2:
        logger.warning(f"Target has no variance for {model_type}, skipping")
        return None

    # 3. Train model with walk-forward CV
    try:
        dates = df["entry_ts"] if "entry_ts" in df.columns else None
        model, train_auc, holdout_auc = train_model(X, y, dates=dates)
    except Exception as e:
        logger.error(f"Model training failed for {model_type}: {e}")
        return None

    # 4. Save model to disk for MLScorer (only save if AUC is acceptable)
    if holdout_auc >= 0.55:
        save_model(model, model_type, feature_names)
    else:
        logger.warning(
            f"Skipping model save for {model_type}: AUC {holdout_auc:.3f} below threshold",
            extra={"event": "model_save_skipped", "model_type": model_type, "auc": holdout_auc},
        )

    # 5. Extract patterns
    top_features = extract_feature_importance(model, feature_names, top_k=10)
    top_rules = extract_tree_rules(model, feature_names, X, y, top_k=5)

    # 5. Detect drift
    last_week = await get_last_week_importance(model_type)
    degraded = []
    for feat in top_features[:5]:
        if feat.feature in last_week:
            old_imp = last_week[feat.feature]
            delta = (feat.importance - old_imp) / max(old_imp, 0.01)
            feat.delta_vs_last_week = delta
            if delta < -0.3:  # 30% drop
                degraded.append(f"{feat.feature} (dropped {abs(delta):.0%})")

    # 6. Build insight
    insight = PatternInsight(
        insight_id=hashlib.sha256(f"{model_type}_{datetime.now().isoformat()}".encode()).hexdigest()[:16],
        created_at_utc=datetime.now(timezone.utc),
        model_type=model_type,
        training_window_days=window_days,
        sample_size=len(df),
        positive_rate=float(y.mean()),
        train_auc=train_auc,
        holdout_auc=holdout_auc,
        top_rules=top_rules,
        top_features=top_features,
        degraded_features=degraded,
        metadata={"bucket": bucket_name, "target": target_name},
    )

    # 7. Persist
    await persist_insight(insight)

    logger.info(
        f"Pattern mining complete for {model_type}",
        extra={
            "event": "ml_mining_complete",
            "model_type": model_type,
            "auc": holdout_auc,
            "sample_size": len(df),
            "rules_extracted": len(top_rules),
        },
    )

    return insight


async def run_all_pattern_mining() -> MLInsightsSummary:
    """
    Run pattern mining for all bucket x target combinations.

    Produces 4 buckets x 4 targets = 16 entry models + 4 exit models.
    """
    insights: Dict[str, PatternInsight] = {}
    alerts: List[str] = []

    # Train entry models: Iterate over each bucket x target
    # Include quick_winner which is dynamically generated per bucket
    all_target_names = list(TARGETS.keys()) + ["quick_winner"]
    for bucket_name, bucket_config in TRADE_BUCKET_CONFIGS.items():
        for target_name in all_target_names:
            try:
                insight = await run_pattern_mining(
                    target_name=target_name,
                    bucket_name=bucket_name,
                    bucket_config=bucket_config,
                )
                if insight:
                    insights[insight.model_type] = insight

                    # Generate alerts
                    if insight.holdout_auc < 0.55:
                        alerts.append(f"{insight.model_type}: Model AUC very low ({insight.holdout_auc:.2f})")
                    if insight.degraded_features:
                        alerts.append(
                            f"{insight.model_type}: Feature drift in {len(insight.degraded_features)} features"
                        )

            except Exception as e:
                model_type = f"{bucket_name}_{target_name}"
                logger.error(f"Pattern mining failed for {model_type}: {e}", exc_info=True)
                alerts.append(f"{model_type}: Mining failed - {str(e)[:50]}")

    # Train exit models: Retrain exit classifiers for each bucket
    try:
        from orion.ml.exit_classifier import train_all_exit_classifiers

        logger.info("Training exit classifiers for all buckets")
        force_schema_refresh, refresh_each_bucket, refresh_source = (
            _exit_classifier_schema_refresh_config_details_from_env()
        )
        refresh_mode = _exit_classifier_schema_refresh_mode(force_schema_refresh, refresh_each_bucket)
        logger.info(
            "Exit classifier schema refresh config: force=%s refresh_each_bucket=%s",
            force_schema_refresh,
            refresh_each_bucket,
            extra={
                "event": "exit_training_schema_refresh_config_resolved",
                "refresh_mode": refresh_mode,
                "refresh_source": refresh_source,
                "force_schema_refresh": force_schema_refresh,
                "refresh_each_bucket": refresh_each_bucket,
            },
        )
        exit_results = await train_all_exit_classifiers(
            force_schema_refresh=force_schema_refresh,
            refresh_each_bucket=refresh_each_bucket,
        )

        for bucket, data in exit_results.items():
            if data.get("auc", 0) < 0.55:
                alerts.append(f"{bucket}_exit: Exit AUC low ({data.get('auc', 0):.2f})")
            logger.info(
                f"Exit classifier trained for {bucket}: AUC={data.get('auc', 0):.3f}",
                extra={"bucket": bucket, "auc": data.get("auc")},
            )
    except Exception as e:
        logger.error(f"Exit classifier training failed: {e}", exc_info=True)
        alerts.append(f"exit_classifiers: Training failed - {str(e)[:50]}")

    summary = MLInsightsSummary(
        generated_at_utc=datetime.now(timezone.utc),
        insights=insights,
        alerts=alerts,
    )

    logger.info(
        f"All pattern mining complete: {len(insights)} entry models + 4 exit models trained",
        extra={"models_count": len(insights), "alerts_count": len(alerts)},
    )

    return summary
