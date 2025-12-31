"""
ML Scorer for flow events.

Scores every flow event with a trained LightGBM model.
Replaces rule-based pre-filtering with pure ML scoring.
"""

import os
import pickle
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.ml.scorer")

# Default model path
MODEL_DIR = Path(os.getenv("ORION_MODEL_DIR", "/app/models"))
DEFAULT_MODEL_NAME = "flow_scorer_v1.pkl"

# Score threshold for generating candidates (adjustable via solver config)
DEFAULT_SCORE_THRESHOLD = 0.5

# Features used for scoring (must match training features)
SCORING_FEATURES = [
    "premium_usd",
    "dte",
    "iv",
    "volume_contract",
    "open_interest",
    "underlying_price",
    "strike",
    "size_contracts",
    # Derived features
    "moneyness",  # strike / underlying_price
    "volume_oi_ratio",
    "premium_per_contract",
]


class MLScorer:
    """
    Scores flow events using a trained LightGBM model.

    If no model is available, uses a heuristic baseline scorer.
    """

    def __init__(self, model_path: Optional[Path] = None) -> None:
        self.model = None
        self.model_path = model_path or (MODEL_DIR / DEFAULT_MODEL_NAME)
        self.use_heuristic = True

        self._load_model()

    def _load_model(self) -> None:
        """Load trained model if available."""
        if self.model_path.exists():
            try:
                with open(self.model_path, "rb") as f:
                    self.model = pickle.load(f)
                self.use_heuristic = False
                logger.info(
                    f"Loaded ML model from {self.model_path}",
                    extra={"event": "model_loaded", "path": str(self.model_path)},
                )
            except Exception as e:
                logger.warning(
                    f"Failed to load model, using heuristic: {e}",
                    extra={"event": "model_load_failed", "error": str(e)},
                )
        else:
            logger.info(
                f"No model found at {self.model_path}, using heuristic scorer",
                extra={"event": "using_heuristic"},
            )

    def extract_features(self, flow: Dict[str, Any]) -> Dict[str, float]:
        """
        Extract features from a flow event for scoring.
        """
        premium = float(flow.get("premium_usd") or 0)
        underlying = float(flow.get("underlying_price") or 0)
        strike = float(flow.get("strike") or 0)
        size = int(flow.get("size_contracts") or 0)
        option_price = float(flow.get("option_price") or 0)
        volume = float(flow.get("volume_contract") or 0)
        oi = float(flow.get("open_interest") or 0)

        return {
            "premium_usd": premium,
            "dte": int(flow.get("dte") or 0),
            "iv": float(flow.get("iv") or 0),
            "volume_contract": volume,
            "open_interest": oi,
            "underlying_price": underlying,
            "strike": strike,
            "size_contracts": size,
            "moneyness": strike / underlying if underlying > 0 else 1.0,
            "volume_oi_ratio": volume / oi if oi > 0 else 0,
            "premium_per_contract": premium / size if size > 0 else 0,
        }

    def score(self, flow: Dict[str, Any]) -> float:
        """
        Score a flow event. Returns probability [0, 1].

        Higher score = more likely to be a profitable trade.
        """
        features = self.extract_features(flow)

        if self.use_heuristic:
            return self._heuristic_score(features, flow)

        # Use trained model
        try:
            feature_vector = np.array([[features[f] for f in SCORING_FEATURES]])
            prob = self.model.predict_proba(feature_vector)[0][1]
            return float(prob)
        except Exception as e:
            logger.warning(f"Model scoring failed, using heuristic: {e}")
            return self._heuristic_score(features, flow)

    def _heuristic_score(self, features: Dict[str, float], flow: Dict[str, Any]) -> float:
        """
        Heuristic baseline scorer when no trained model is available.

        Signals with high premium, sweeps, and good aggressor alignment
        get higher scores.
        """
        score = 0.3  # Base score

        # Premium factor (log scale)
        premium = features["premium_usd"]
        if premium >= 500000:
            score += 0.25
        elif premium >= 100000:
            score += 0.15
        elif premium >= 50000:
            score += 0.10
        elif premium >= 25000:
            score += 0.05

        # Sweep bonus
        is_sweep = str(flow.get("is_sweep", "")).lower() == "true"
        if is_sweep:
            score += 0.15

        # Aggressor alignment (ASK = bullish intent for calls)
        aggressor = flow.get("aggressor", "")
        put_call = flow.get("put_call", "")
        if (put_call == "C" and aggressor == "ASK") or (put_call == "P" and aggressor == "BID"):
            score += 0.10

        # Volume/OI ratio (unusual activity)
        vol_oi = features.get("volume_oi_ratio", 0)
        if vol_oi > 2.0:
            score += 0.10
        elif vol_oi > 1.0:
            score += 0.05

        # Penalize very low premium (noise)
        if premium < 10000:
            score -= 0.20

        return min(max(score, 0.0), 1.0)

    def should_trade(self, flow: Dict[str, Any], threshold: float = DEFAULT_SCORE_THRESHOLD) -> bool:
        """Check if flow score exceeds threshold."""
        return self.score(flow) >= threshold

    def score_batch(self, flows: List[Dict[str, Any]]) -> List[float]:
        """Score multiple flows efficiently."""
        if self.use_heuristic:
            return [self.score(f) for f in flows]

        # Batch scoring with model
        try:
            feature_matrix = np.array([[self.extract_features(f)[feat] for feat in SCORING_FEATURES] for f in flows])
            probs = self.model.predict_proba(feature_matrix)[:, 1]
            return probs.tolist()
        except Exception as e:
            logger.warning(f"Batch scoring failed: {e}")
            return [self.score(f) for f in flows]


# Singleton instance
_scorer: Optional[MLScorer] = None


def get_scorer() -> MLScorer:
    """Get or create the MLScorer singleton."""
    global _scorer
    if _scorer is None:
        _scorer = MLScorer()
    return _scorer
