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
            "current_return_pct": features.current_return_pct,
            "time_held_hours": features.time_held_hours,
            "max_return_so_far": features.max_return_so_far,
            "max_drawdown_so_far": features.max_drawdown_so_far,
            "premium_usd": features.premium_usd,
            "dte_at_entry": float(features.dte_at_entry),
            "is_sweep": 1.0 if features.is_sweep else 0.0,
            "iv_rank_at_entry": float(features.iv_rank_at_entry or 50),
            "vix_at_entry": float(features.vix_at_entry or 20),
            "gex_at_entry": float(features.gex_at_entry or 0),
            "market_tide_30m": float(features.market_tide_30m or 0),
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

    # Build column list for query
    return_cols = ", ".join([f"return_at_{cp[0]}" for cp in checkpoints])

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
            -- Entry context
            premium_usd, dte, is_sweep,
            iv_rank_at_entry, vix_at_entry,
            trend_regime_at_entry, vol_regime_at_entry,
            gex_at_entry, market_tide_30m,

            -- Returns at bucket-specific checkpoints
            {return_cols},

            -- Outcome
            max_return_pct, max_drawdown_pct
        FROM price_target_labels
        WHERE trade_type = '{trade_type}'
        AND max_return_pct IS NOT NULL
    """

    async def run_query(session: Any) -> List[Any]:
        from sqlalchemy import text

        result = await session.execute(text(query))
        return result.mappings().all()

    rows = await db_query(run_query)

    if not rows:
        logger.warning(f"No training data for bucket {bucket}")
        return np.array([]), np.array([]), []

    feature_names = [
        "current_return_pct",
        "time_held_hours",
        "premium_usd",
        "dte_at_entry",
        "is_sweep",
        "iv_rank_at_entry",
        "vix_at_entry",
        "gex_at_entry",
        "market_tide_30m",
    ]

    X_list = []
    y_list = []

    for row in rows:
        max_return = float(row["max_return_pct"] or 0)
        if max_return <= 0:
            continue  # Skip losing trades for exit timing

        threshold = max_return * GOOD_EXIT_THRESHOLD

        # Create sample for each checkpoint
        for col_suffix, hours, desc in checkpoints:
            col_name = f"return_at_{col_suffix}"
            checkpoint_return = row.get(col_name)
            if checkpoint_return is None:
                continue

            checkpoint_return = float(checkpoint_return)

            # Features
            features = [
                checkpoint_return,  # current return at checkpoint
                hours,  # time held
                float(row.get("premium_usd") or 0),
                int(row.get("dte") or 0),
                1 if row.get("is_sweep") else 0,
                float(row.get("iv_rank_at_entry") or 50),
                float(row.get("vix_at_entry") or 20),
                float(row.get("gex_at_entry") or 0),
                float(row.get("market_tide_30m") or 0),
            ]

            # Target: was this a good exit point?
            # Good exit = captured >= 80% of max return
            target = 1 if checkpoint_return >= threshold else 0

            X_list.append(features)
            y_list.append(target)

    logger.info(
        f"Built training data for {bucket}: {len(X_list)} samples",
        extra={"event": "exit_training_data", "bucket": bucket, "samples": len(X_list)},
    )

    return np.array(X_list), np.array(y_list), feature_names


async def train_bucket_exit_classifier(bucket: str) -> Optional[Dict[str, Any]]:
    """Train exit classifier for a specific bucket."""
    try:
        from lightgbm import LGBMClassifier
    except ImportError:
        logger.error("LightGBM not installed")
        return None

    X, y, feature_names = await build_bucket_training_data(bucket)

    if len(X) < MIN_SAMPLES:
        logger.warning(
            f"Insufficient exit training data for {bucket}: {len(X)} samples",
            extra={"bucket": bucket, "samples": len(X)},
        )
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
