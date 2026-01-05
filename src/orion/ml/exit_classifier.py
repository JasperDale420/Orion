"""
Exit Classifier.

ML classifier to predict optimal exit timing for open positions.
Uses price_target_labels data to learn when holding longer improves outcome.
"""

import logging
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import numpy as np

from orion.shared.db_utils import db_query

logger = logging.getLogger(__name__)

# Checkpoint hours for exit decisions
CHECKPOINT_HOURS = [1, 2, 4]

# Threshold for "good exit" - captured this % of max return
GOOD_EXIT_THRESHOLD = 0.8


@dataclass
class ExitFeatures:
    """Features for exit decision at a checkpoint."""

    # Position state
    current_return_pct: float
    time_held_hours: float
    max_return_so_far: float
    max_drawdown_so_far: float

    # Entry context (for reference)
    premium_usd: float
    dte_at_entry: int
    is_sweep: bool

    # Market context at entry (extend later with checkpoint context)
    iv_rank_at_entry: Optional[float]
    vix_at_entry: Optional[float]
    trend_regime: Optional[str]
    vol_regime: Optional[str]


@dataclass
class ExitPrediction:
    """Exit classifier output."""

    should_exit: bool
    confidence: float
    reasoning: str


class ExitClassifier:
    """
    Predicts whether to exit a position at a given checkpoint.

    Training target: Did exiting at checkpoint T capture >= 80% of max return?
    If yes -> should_exit = True (good time to exit)
    If no -> should_exit = False (holding longer was better)
    """

    def __init__(self) -> None:
        self.model: Any = None
        self.use_heuristic = True
        self._load_model()

    def _load_model(self) -> None:
        """Load trained model or fall back to heuristic."""
        # TODO: Load from ORION_MODEL_DIR/exit_classifier_v1.pkl
        logger.info("Using heuristic exit classifier (no trained model)")

    def predict(self, features: ExitFeatures) -> ExitPrediction:
        """Predict whether to exit given current position state."""
        if self.use_heuristic:
            return self._heuristic_predict(features)

        # TODO: ML prediction
        return self._heuristic_predict(features)

    def _heuristic_predict(self, features: ExitFeatures) -> ExitPrediction:
        """
        Rule-based exit logic as baseline.

        Exit signals:
        1. Hit 50%+ return -> take profits
        2. Drawdown > 20% -> cut losses
        3. 0DTE and time > 2h -> theta decay accelerating
        4. Return going negative after being positive -> momentum shift
        """
        reasons = []
        exit_score = 0.0

        # Profit target hit
        if features.current_return_pct >= 50:
            exit_score += 0.5  # Strong exit signal
            reasons.append(f"Hit {features.current_return_pct:.0f}% return")
        elif features.current_return_pct >= 25:
            exit_score += 0.2
            reasons.append(f"Solid {features.current_return_pct:.0f}% gain")

        # Stop loss
        if features.current_return_pct <= -20:
            exit_score += 0.5
            reasons.append(f"Stop loss at {features.current_return_pct:.0f}%")

        # 0DTE theta decay
        if features.dte_at_entry == 0 and features.time_held_hours >= 2:
            exit_score += 0.5  # Theta decay accelerating
            reasons.append("0DTE theta acceleration")

        # Momentum reversal (was up, now down)
        if features.max_return_so_far > 10 and features.current_return_pct < 0:
            exit_score += 0.4
            reasons.append("Momentum reversal")

        # Drawdown from peak
        drawdown_from_peak = features.max_return_so_far - features.current_return_pct
        if drawdown_from_peak > 15 and features.max_return_so_far > 20:
            exit_score += 0.3
            reasons.append(f"Gave back {drawdown_from_peak:.0f}% from peak")

        should_exit = exit_score >= 0.5

        return ExitPrediction(
            should_exit=should_exit,
            confidence=min(exit_score, 1.0),
            reasoning="; ".join(reasons) if reasons else "No exit signal",
        )

    def predict_batch(
        self,
        features_list: List[ExitFeatures],
    ) -> List[ExitPrediction]:
        """Predict exit for multiple positions."""
        return [self.predict(f) for f in features_list]


async def build_training_data() -> Tuple[np.ndarray, np.ndarray]:
    """
    Build training dataset from price_target_labels.

    For each checkpoint (1h, 2h, 4h), create a sample:
    - Features: return at checkpoint, time held, entry context
    - Target: 1 if checkpoint return >= 80% of max_return, else 0
    """
    query = """
        SELECT
            -- Entry context
            premium_usd, dte, is_sweep,
            iv_rank_at_entry, vix_at_entry,
            trend_regime_at_entry, vol_regime_at_entry,

            -- Returns at checkpoints
            return_at_1h, return_at_2h, return_at_4h,

            -- Outcome
            max_return_pct, max_drawdown_pct
        FROM price_target_labels
        WHERE max_return_pct IS NOT NULL
        AND return_at_1h IS NOT NULL
    """

    rows = await db_query(query)

    if not rows:
        logger.warning("No training data found in price_target_labels")
        return np.array([]), np.array([])

    X_list = []
    y_list = []

    for row in rows:
        max_return = float(row["max_return_pct"] or 0)
        threshold = max_return * GOOD_EXIT_THRESHOLD

        # Create sample for each checkpoint
        for hours, return_col in [(1, "return_at_1h"), (2, "return_at_2h"), (4, "return_at_4h")]:
            checkpoint_return = float(row[return_col] or 0)

            # Features
            features = [
                checkpoint_return,  # current return
                hours,  # time held
                float(row["premium_usd"] or 0),
                int(row["dte"] or 0),
                1 if row["is_sweep"] else 0,
                float(row["iv_rank_at_entry"] or 50),
                float(row["vix_at_entry"] or 20),
            ]

            # Target: was this a good exit point?
            target = 1 if checkpoint_return >= threshold else 0

            X_list.append(features)
            y_list.append(target)

    logger.info(f"Built training data: {len(X_list)} samples")

    return np.array(X_list), np.array(y_list)


async def train_exit_classifier() -> None:
    """Train and save exit classifier model."""
    try:
        from lightgbm import LGBMClassifier
    except ImportError:
        logger.error("LightGBM not installed, cannot train exit classifier")
        return

    X, y = await build_training_data()

    if len(X) < 100:
        logger.warning(f"Insufficient training data: {len(X)} samples (need 100+)")
        return

    # Train model
    model = LGBMClassifier(
        n_estimators=100,
        max_depth=5,
        learning_rate=0.1,
        random_state=42,
    )

    model.fit(X, y)

    # Evaluate
    accuracy = model.score(X, y)
    logger.info(f"Exit classifier training accuracy: {accuracy:.2%}")

    # TODO: Save model to ORION_MODEL_DIR/exit_classifier_v1.pkl

    return model


# Singleton
_exit_classifier: Optional[ExitClassifier] = None


def get_exit_classifier() -> ExitClassifier:
    """Get or create exit classifier singleton."""
    global _exit_classifier
    if _exit_classifier is None:
        _exit_classifier = ExitClassifier()
    return _exit_classifier
