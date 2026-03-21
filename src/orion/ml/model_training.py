"""LightGBM model training, validation, and serialization for pattern mining."""

import pickle
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from orion.config import system_settings
from orion.ml.feature_config import CATEGORICAL_COLUMNS
from orion.ml.schemas import FeatureImportance, TreeRule
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.ml.model_training")

MODEL_DIR = system_settings.model_dir


def prepare_features(df: Any, feature_names: list[str]) -> tuple[Any, Any, dict[str, list[str]]]:
    """Prepare feature matrix X from dataframe.

    Returns:
        Tuple of (X feature matrix, feature_names used, categorical_mappings)
    """
    import pandas as pd

    X = df[feature_names].copy()  # noqa: N806

    for col in feature_names:
        if col not in CATEGORICAL_COLUMNS:
            X[col] = pd.to_numeric(X[col], errors="coerce")

    categorical_mappings: dict[str, list[str]] = {}
    for col in CATEGORICAL_COLUMNS:
        if col in X.columns:
            cat = pd.Categorical(X[col])
            categorical_mappings[col] = list(cat.categories.astype(str))
            X[col] = cat.codes  # noqa: N806

    X = X.fillna(-999)  # noqa: N806

    return X, feature_names, categorical_mappings


def train_model(
    X: Any,  # noqa: N803
    y: Any,
    test_size: float = 0.2,
    use_walk_forward: bool = True,
    n_splits: int = 5,
    dates: Any = None,
) -> tuple[Any, float, float]:
    """Train LightGBM classifier using walk-forward or random validation.

    Returns:
        Tuple of (model, train_auc, holdout_auc)
    """
    params = {
        "objective": "binary",
        "metric": "auc",
        "boosting_type": "gbdt",
        "num_leaves": 16,
        "max_depth": 4,
        "learning_rate": 0.1,
        "n_estimators": 100,
        "min_child_samples": 20,
        "verbose": -1,
    }

    if use_walk_forward and dates is not None:
        return _train_walk_forward(X, y, dates, params, n_splits)

    return _train_random_split(X, y, params, test_size)


def _train_random_split(X: Any, y: Any, params: dict, test_size: float) -> tuple[Any, float, float]:  # noqa: N803
    """Train with random train/test split (legacy behavior)."""
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import train_test_split

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=test_size, random_state=42, stratify=y)  # noqa: N806

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


def _train_walk_forward(X: Any, y: Any, dates: Any, params: dict, n_splits: int = 5) -> tuple[Any, float, float]:  # noqa: N803
    """Train with walk-forward (expanding window) validation.

    Prevents look-ahead bias by always training on past data and testing on future data.
    """
    import lightgbm as lgb
    import numpy as np
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import TimeSeriesSplit

    sort_idx = np.argsort(dates)
    x_sorted = X.iloc[sort_idx] if hasattr(X, "iloc") else X[sort_idx]
    y_sorted = y.iloc[sort_idx] if hasattr(y, "iloc") else y[sort_idx]

    tscv = TimeSeriesSplit(n_splits=n_splits)

    fold_aucs = []

    for fold, (train_idx, test_idx) in enumerate(tscv.split(x_sorted)):
        x_train, x_test = x_sorted.iloc[train_idx], x_sorted.iloc[test_idx]
        y_train, y_test = y_sorted.iloc[train_idx], y_sorted.iloc[test_idx]

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

    final_model = lgb.LGBMClassifier(**params)
    final_model.fit(x_sorted, y_sorted)

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

    return final_model, train_auc, avg_auc


def save_model(
    model: Any,
    model_type: str,
    feature_names: list[str],
    categorical_mappings: dict[str, list[str]] | None = None,
) -> Path | None:
    """Save trained model to disk for MLScorer to load."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    model_path = MODEL_DIR / f"{model_type}.pkl"

    try:
        model_data = {
            "model": model,
            "feature_names": feature_names,
            "model_type": model_type,
            "created_at": datetime.now(UTC).isoformat(),
            "categorical_mappings": categorical_mappings or {},
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
    feature_names: list[str],
    top_k: int = 10,
) -> list[FeatureImportance]:
    """Extract top feature importances from trained model."""
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
    feature_names: list[str],
    X: Any,  # noqa: N803
    y: Any,
    top_k: int = 5,
) -> list[TreeRule]:
    """Extract human-readable rules from decision tree splits."""
    import pandas as pd

    rules = []

    leaf_indices = model.predict(X, pred_leaf=True)

    if len(leaf_indices.shape) > 1:
        leaf_indices = leaf_indices[:, 0]

    df_leaves = pd.DataFrame({"leaf": leaf_indices, "target": y.values})
    leaf_stats = (
        df_leaves.groupby("leaf")
        .agg(
            hit_rate=("target", "mean"),
            sample_size=("target", "count"),
        )
        .reset_index()
    )

    leaf_stats = leaf_stats[leaf_stats["sample_size"] >= 10]

    base_rate = y.mean()
    leaf_stats["deviation"] = abs(leaf_stats["hit_rate"] - base_rate)
    leaf_stats = leaf_stats.sort_values("deviation", ascending=False).head(top_k)

    for _, row in leaf_stats.iterrows():
        mask = leaf_indices == row["leaf"]
        leaf_X = X[mask]  # noqa: N806

        conditions = []
        for feat in feature_names[:3]:
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
                confidence=min(1.0, row["sample_size"] / 50),
            )
        )

    return rules
