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
MODEL_DIR = Path(os.getenv("ORION_MODEL_DIR", "/app/models"))


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
    # Darkpool feature (1h lookback from entry)
    "darkpool_volume_1h",
    # NOTE: Removed outcome features that cause data leakage:
    # - max_return_pct, max_drawdown_pct, time_to_max_seconds
    # - holding_period_seconds, return_at_1h/2h/4h, first_exit_type
    # - opposing_flow_count (during holding period, not at entry)
]

CATEGORICAL_COLUMNS = [
    "put_call",
    # NOTE: trade_type removed - it's used for filtering, not prediction
    "vol_regime_at_entry",
    "risk_regime_at_entry",
    "session_regime_at_entry",
    "trend_regime_at_entry",
    "vix_regime_at_entry",
    "market_tide_direction",
]

# Target definitions - 4 targets for diverse signal dimensions
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
    "quick_winner": """
        CASE WHEN hit_50_pct_ts IS NOT NULL
             AND time_to_50_pct_seconds IS NOT NULL
             AND time_to_50_pct_seconds < 3600
             AND (hit_stop_20_pct_ts IS NULL OR hit_50_pct_ts < hit_stop_20_pct_ts)
        THEN 1 ELSE 0 END
    """,
}

# Trade bucket configurations with bucket-specific lookback windows
TRADE_BUCKET_CONFIGS = {
    "0DTE": {
        "filter": "trade_type = '0DTE'",
        "window_days": 10,  # Short lookback - fast-changing dynamics
        "min_samples": 50,
        "description": "Same-day expiry options",
    },
    "SHORT_SWING": {
        "filter": "trade_type = 'SHORT_SWING'",
        "window_days": 20,  # 1-3 DTE trades
        "min_samples": 50,
        "description": "1-3 day expiry options",
    },
    "SWING": {
        "filter": "trade_type = 'SWING'",
        "window_days": 45,  # 3-14 DTE trades
        "min_samples": 30,
        "description": "3-14 day expiry options",
    },
    "POSITION": {
        "filter": "trade_type = 'POSITION'",
        "window_days": 90,  # Long-term trades need more history
        "min_samples": 20,
        "description": "14+ day expiry options",
    },
}


async def fetch_training_data(
    window_days: int = 30,
    min_samples: int = 100,
    trade_type_filter: Optional[str] = None,
) -> Tuple[Any, List[str]]:
    """
    Fetch training data from price_target_labels.

    Args:
        window_days: Number of days to look back
        min_samples: Minimum samples required
        trade_type_filter: Optional SQL filter for trade_type (e.g., "trade_type = '0DTE'")

    Returns:
        Tuple of (pandas DataFrame, list of feature names)
    """
    import pandas as pd

    feature_cols = ", ".join(FEATURE_COLUMNS + CATEGORICAL_COLUMNS)
    target_cols = ", ".join([f"({sql}) as target_{name}" for name, sql in TARGETS.items()])

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
        ["event_id", "entry_ts"] + FEATURE_COLUMNS + CATEGORICAL_COLUMNS + [f"target_{name}" for name in TARGETS.keys()]
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
) -> Tuple[Any, float, float]:
    """
    Train LightGBM classifier.

    Returns:
        Tuple of (model, train_auc, holdout_auc)
    """
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import train_test_split

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=test_size, random_state=42, stratify=y)

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

    model = lgb.LGBMClassifier(**params)
    model.fit(X_train, y_train)

    # Evaluate
    train_pred = model.predict_proba(X_train)[:, 1]
    test_pred = model.predict_proba(X_test)[:, 1]

    train_auc = roc_auc_score(y_train, train_pred)
    holdout_auc = roc_auc_score(y_test, test_pred)

    logger.info(
        f"Model trained: train_auc={train_auc:.3f}, holdout_auc={holdout_auc:.3f}",
        extra={"event": "ml_model_train", "train_auc": train_auc, "holdout_auc": holdout_auc},
    )

    return model, train_auc, holdout_auc


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

    logger.info(
        f"Starting pattern mining for {model_type}",
        extra={"bucket": bucket_name, "target": target_name, "window_days": window_days},
    )

    # 1. Fetch data (filtered by bucket)
    df, feature_names = await fetch_training_data(
        window_days=window_days,
        min_samples=min_samples,
        trade_type_filter=trade_filter,
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

    # 3. Train model
    try:
        model, train_auc, holdout_auc = train_model(X, y)
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
    for bucket_name, bucket_config in TRADE_BUCKET_CONFIGS.items():
        for target_name in TARGETS.keys():
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
        exit_results = await train_all_exit_classifiers()

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

