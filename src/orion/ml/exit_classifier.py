"""
Exit Classifier v2 - Bucket-Specific Exit Timing.

ML classifier to predict optimal exit timing for open positions.
Uses price_target_labels data with bucket-appropriate checkpoints.

Buckets and their time horizons:
- 0DTE: Minutes (5m, 10m, 15m, 30m, 1h)
- SHORT_SWING: Hours (30m, 1h, 2h, 4h, 8h)
- SWING: Hours to days (1h, 4h, 8h, EOD, 1d, 2d)
- POSITION: Days to weeks (1d, 2d, 3d, 1w)
"""

import os
import pickle
from math import isnan
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

from orion.shared.db_utils import db_query
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.ml.exit_classifier")

MODEL_DIR = Path(os.getenv("ORION_MODEL_DIR", "/app/models"))


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
    bucket: str

    # Market context at entry
    iv_rank_at_entry: Optional[float] = None
    vix_at_entry: Optional[float] = None
    trend_regime: Optional[str] = None
    vol_regime: Optional[str] = None
    gex_at_entry: Optional[float] = None
    market_tide_30m: Optional[float] = None

    # Entry Greeks
    delta_at_entry: Optional[float] = None
    theta_at_entry: Optional[float] = None
    iv_at_entry: Optional[float] = None
    ask_side_ratio: Optional[float] = None

    # Checkpoint-specific (current position state)
    delta_at_checkpoint: Optional[float] = None
    gamma_at_checkpoint: Optional[float] = None
    theta_at_checkpoint: Optional[float] = None
    iv_at_checkpoint: Optional[float] = None
    dte_at_checkpoint: Optional[float] = None
    time_value_pct: Optional[float] = None
    theta_decay_pct: Optional[float] = None


@dataclass
class ExitPrediction:
    """Exit classifier output."""

    should_exit: bool
    confidence: float
    reasoning: str
    checkpoint: Optional[str] = None


class BucketExitClassifier:
    """
    Bucket-specific exit classifier.

    Each bucket has its own model trained on appropriate time horizons.
    Falls back to heuristic when no model is available.
    """

    def __init__(self) -> None:
        self.models: Dict[str, Any] = {}  # bucket -> model_data
        self.feature_names: Dict[str, List[str]] = {}
        self._load_models()

    def _load_models(self) -> None:
        """Load all bucket-specific exit models."""
        if not MODEL_DIR.exists():
            logger.info("Model directory does not exist, using heuristic")
            return

        loaded_count = 0
        for bucket in BUCKET_CHECKPOINTS.keys():
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
        bucket = features.bucket

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

    def _features_to_dict(self, features: ExitFeatures) -> Dict[str, float]:
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
            reasons.append(f"Hit {features.current_return_pct:.0f}% (TP={thresholds['take_profit']}%)")
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

    def _get_bucket_thresholds(self, bucket: str) -> Dict[str, float]:
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

    def _calculate_time_urgency(self, features: ExitFeatures, thresholds: Dict) -> float:
        """Calculate time-based exit urgency."""
        max_hold = thresholds["max_hold_hours"]
        time_pct = features.time_held_hours / max_hold

        # 0DTE has high theta decay at end of day
        if features.bucket == "0DTE":
            if time_pct >= 0.8:  # Last 20% of allowed hold time
                return 0.5
            elif time_pct >= 0.6:
                return 0.2
        else:
            # Other buckets: gentle time pressure
            if time_pct >= 0.9:
                return 0.3
            elif time_pct >= 0.75:
                return 0.1

        return 0.0

    def predict_batch(self, features_list: List[ExitFeatures]) -> List[ExitPrediction]:
        """Predict exit for multiple positions."""
        return [self.predict(f) for f in features_list]


async def build_bucket_training_data(bucket: str) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """
    Build training dataset for a specific bucket.

    Uses bucket-appropriate checkpoints from price_target_labels.
    """
    checkpoints = BUCKET_CHECKPOINTS.get(bucket, [])
    if not checkpoints:
        logger.warning(f"No checkpoints defined for bucket {bucket}")
        return np.array([]), np.array([]), []

    # Build column list for query - include returns, Greeks, IV, and time value at each checkpoint
    return_cols = ", ".join([f"return_at_{cp[0]}" for cp in checkpoints])
    delta_cols = ", ".join([f"delta_at_{cp[0]}" for cp in checkpoints])
    gamma_cols = ", ".join([f"gamma_at_{cp[0]}" for cp in checkpoints])
    theta_cols = ", ".join([f"theta_at_{cp[0]}" for cp in checkpoints])
    iv_cols = ", ".join([f"iv_at_{cp[0]}" for cp in checkpoints])
    dte_cols = ", ".join([f"dte_at_{cp[0]}" for cp in checkpoints])
    time_value_cols = ", ".join([f"time_value_pct_at_{cp[0]}" for cp in checkpoints])
    theta_decay_cols = ", ".join([f"theta_decay_pct_at_{cp[0]}" for cp in checkpoints])

    # Determine trade_type filter
    trade_type_map = {
        "0DTE": "0DTE",
        "SHORT_SWING": "SHORT_SWING",
        "SWING": "SWING",
        "POSITION": "POSITION",
    }
    trade_type = trade_type_map.get(bucket, bucket)

    query = f"""
        SELECT
            -- Identifier for window feature lookup
            p.ticker,
            p.entry_ts,

            -- Entry context
            COALESCE(p.premium_usd, 0) as premium_usd,
            COALESCE(p.dte, 0) as dte,
            COALESCE(p.is_sweep, false) as is_sweep,
            COALESCE(p.iv_rank_at_entry, 50) as iv_rank_at_entry,
            COALESCE(p.vix_at_entry, 20) as vix_at_entry,
            p.trend_regime_at_entry, p.vol_regime_at_entry,
            COALESCE(p.gex_at_entry, 0) as gex_at_entry,
            COALESCE(p.market_tide_30m, 0) as market_tide_30m,
            COALESCE(p.delta_at_entry, 0) as delta_at_entry,
            COALESCE(p.gamma_at_entry, 0) as gamma_at_entry,
            COALESCE(p.theta_at_entry, 0) as theta_at_entry,
            COALESCE(p.iv_at_entry, 0) as iv_at_entry,
            COALESCE(p.ask_side_ratio, 0) as ask_side_ratio,

            -- Returns at bucket-specific checkpoints
            {return_cols},

            -- Greeks at checkpoints (for exit timing)
            {delta_cols},
            {gamma_cols},
            {theta_cols},
            {iv_cols},
            {dte_cols},
            {time_value_cols},
            {theta_decay_cols},

            -- Outcome
            p.max_return_pct, p.max_drawdown_pct,

            -- Window features (1h context at entry)
            COALESCE(w.features_by_period->'1h'->>'call_put_imbalance', '0') as window_call_put_imbalance_1h,
            COALESCE(w.features_by_period->'1h'->>'sweep_ratio', '0') as window_sweep_ratio_1h,
            COALESCE(w.features_by_period->'1h'->>'flow_count', '0') as window_flow_count_1h,

            -- Window features (1d context at entry)
            COALESCE(w.features_by_period->'1d'->>'call_put_imbalance', '0') as window_call_put_imbalance_1d,
            COALESCE(w.features_by_period->'1d'->>'sweep_ratio', '0') as window_sweep_ratio_1d,
            COALESCE(w.features_by_period->'1d'->>'dp_volume', '0') as window_dp_volume_1d,
            COALESCE(w.features_by_period->'1d'->>'call_put_ratio', '0') as window_call_put_ratio_1d,

            -- Window features (1w context at entry)
            COALESCE(w.features_by_period->'1w'->>'call_put_imbalance', '0') as window_call_put_imbalance_1w,
            COALESCE(w.features_by_period->'1w'->>'sweep_ratio', '0') as window_sweep_ratio_1w,
            COALESCE(w.features_by_period->'1w'->>'call_put_ratio', '0') as window_call_put_ratio_1w

        FROM price_target_labels p
        -- Join latest window features for 1h, 1d, 1w periods in one lateral lookup
        LEFT JOIN LATERAL (
            SELECT jsonb_object_agg(period, features) as features_by_period
            FROM (
                SELECT DISTINCT ON (period) period, features
                FROM gold_feature_windows
                WHERE ticker = p.ticker
                  AND period IN ('1h', '1d', '1w')
                  AND window_end_ts_utc <= p.entry_ts
                ORDER BY period, window_end_ts_utc DESC
            ) latest_by_period
        ) w ON true
        WHERE p.trade_type = :trade_type
        AND p.max_return_pct IS NOT NULL
    """

    async def run_query(session: Any) -> List[Any]:
        from sqlalchemy import text

        result = await session.execute(text(query), {"trade_type": trade_type})
        return result.mappings().all()

    rows = await db_query(run_query)

    if not rows:
        logger.warning(f"No training data for bucket {bucket}")
        return np.array([]), np.array([]), []

    # Expanded feature names - includes checkpoint-specific features and window features
    feature_names = [
        # Position state at checkpoint
        "current_return_pct",
        "time_held_hours",
        # Greeks evolution
        "delta_at_checkpoint",
        "gamma_at_checkpoint",
        "theta_at_checkpoint",
        "iv_at_checkpoint",
        "dte_at_checkpoint",
        # Time value decay
        "time_value_pct",
        "theta_decay_pct",
        # Entry context
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
        # Window features (multi-timeframe flow context)
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
    ]

    X_list = []
    y_list = []

    for row in rows:
        max_return = _safe_float(row.get("max_return_pct"))
        if max_return <= 0:
            continue  # Skip losing trades for exit timing

        threshold = max_return * GOOD_EXIT_THRESHOLD

        # Create sample for each checkpoint
        for col_suffix, hours, _desc in checkpoints:
            col_name = f"return_at_{col_suffix}"
            checkpoint_return = _safe_float(row.get(col_name), default=float("nan"))
            if isnan(checkpoint_return):
                continue

            # Get checkpoint-specific Greeks (safely)
            delta = _safe_float(row.get(f"delta_at_{col_suffix}"))
            gamma = _safe_float(row.get(f"gamma_at_{col_suffix}"))
            theta = _safe_float(row.get(f"theta_at_{col_suffix}"))
            iv = _safe_float(row.get(f"iv_at_{col_suffix}"))
            dte_cp = _safe_float(row.get(f"dte_at_{col_suffix}"))
            time_value_pct = _safe_float(row.get(f"time_value_pct_at_{col_suffix}"))
            theta_decay_pct = _safe_float(row.get(f"theta_decay_pct_at_{col_suffix}"))

            # Features
            features = [
                checkpoint_return,  # current return at checkpoint
                hours,  # time held
                # Greeks at checkpoint
                delta,
                gamma,
                theta,
                iv,
                dte_cp,
                # Time value
                time_value_pct,
                theta_decay_pct,
                _safe_float(row.get("premium_usd")),
                int(_safe_float(row.get("dte"))),
                1.0 if _is_truthy(row.get("is_sweep")) else 0.0,
                _safe_float(row.get("iv_rank_at_entry"), default=50),
                _safe_float(row.get("vix_at_entry"), default=20),
                _safe_float(row.get("gex_at_entry")),
                _safe_float(row.get("market_tide_30m")),
                _safe_float(row.get("delta_at_entry")),
                _safe_float(row.get("theta_at_entry")),
                _safe_float(row.get("iv_at_entry")),
                _safe_float(row.get("ask_side_ratio")),
                # Window features (multi-timeframe flow context)
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

            if len(features) != len(feature_names):
                logger.warning(
                    "Skipping malformed exit training sample due to feature-size mismatch",
                    extra={
                        "event": "exit_training_sample_skipped",
                        "bucket": bucket,
                        "expected_feature_count": len(feature_names),
                        "actual_feature_count": len(features),
                        "checkpoint": col_suffix,
                    },
                )
                continue

            # Target: was this a good exit point?
            # Good exit = captured >= 80% of max return
            target = 1 if checkpoint_return >= threshold else 0

            X_list.append(features)
            y_list.append(target)

    logger.info(
        f"Built training data for {bucket}: {len(X_list)} samples",
        extra={"event": "exit_training_data", "bucket": bucket, "samples": len(X_list)},
    )

    if not X_list:
        return (
            np.empty((0, len(feature_names)), dtype=float),
            np.empty((0,), dtype=int),
            feature_names,
        )

    return np.array(X_list, dtype=float), np.array(y_list, dtype=int), feature_names


async def train_bucket_exit_classifier(bucket: str) -> Optional[Dict[str, Any]]:
    """Train exit classifier for a specific bucket."""
    X, y, feature_names = await build_bucket_training_data(bucket)

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
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

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


async def train_all_exit_classifiers() -> Dict[str, Any]:
    """Train exit classifiers for all buckets."""
    results = {}
    for bucket in BUCKET_CHECKPOINTS.keys():
        logger.info(f"Training exit classifier for {bucket}...")
        result = await train_bucket_exit_classifier(bucket)
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
_exit_classifier: Optional[BucketExitClassifier] = None


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
