"""
ML Scorer for flow events.

Scores every flow event with a trained LightGBM model.
Supports bucket-specific models (0DTE, SHORT_SWING, SWING, POSITION).
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

# Score threshold for generating candidates (adjustable via solver config)
DEFAULT_SCORE_THRESHOLD = 0.5

# Trade bucket configurations matching pattern_miner.py
TRADE_BUCKETS = {
    "0DTE": {"max_dte": 0},
    "SHORT_SWING": {"min_dte": 1, "max_dte": 3},
    "SWING": {"min_dte": 4, "max_dte": 14},
    "POSITION": {"min_dte": 15},
}

# Target for scoring (predict hitting 50% profit target)
DEFAULT_TARGET = "hit_target_50"


def get_trade_bucket(dte: Optional[int]) -> str:
    """Classify a flow into trade bucket based on DTE."""
    if dte is None:
        return "SWING"  # Default bucket
    if dte <= 0:
        return "0DTE"
    elif dte <= 3:
        return "SHORT_SWING"
    elif dte <= 14:
        return "SWING"
    else:
        return "POSITION"


class MLScorer:
    """
    Scores flow events using trained LightGBM models.

    Loads bucket-specific models (e.g., SWING_hit_target_50.pkl) trained
    by pattern_miner.py. Falls back to heuristic scorer when no model exists.
    """

    def __init__(self, target: str = DEFAULT_TARGET) -> None:
        self.target = target
        self.models: Dict[str, Any] = {}  # bucket -> model_data
        self.feature_names: Dict[str, List[str]] = {}  # bucket -> feature names

        self._load_models()

    def _load_models(self) -> None:
        """Load all available bucket-specific models."""
        if not MODEL_DIR.exists():
            logger.info(
                f"Model directory {MODEL_DIR} does not exist, using heuristic scorer",
                extra={"event": "using_heuristic"},
            )
            return

        loaded_count = 0
        for bucket in TRADE_BUCKETS:
            model_type = f"{bucket}_{self.target}"
            model_path = MODEL_DIR / f"{model_type}.pkl"

            if model_path.exists():
                try:
                    with open(model_path, "rb") as f:
                        model_data = pickle.load(f)

                    self.models[bucket] = model_data
                    self.feature_names[bucket] = model_data.get("feature_names", [])
                    loaded_count += 1

                    logger.info(
                        f"Loaded model {model_type}",
                        extra={
                            "event": "model_loaded",
                            "model_type": model_type,
                            "path": str(model_path),
                        },
                    )
                except Exception as e:
                    logger.warning(f"Failed to load model {model_type}: {e}")

        if loaded_count == 0:
            logger.info("No bucket models found, using heuristic scorer")
        else:
            logger.info(
                f"Loaded {loaded_count}/{len(TRADE_BUCKETS)} bucket models",
                extra={"event": "models_loaded", "count": loaded_count},
            )

    def extract_features(self, flow: Dict[str, Any], bucket: str) -> Dict[str, float]:
        """
        Extract features from a flow event for scoring.
        Uses feature names from the model if available.
        """
        feature_names = self.feature_names.get(bucket, [])

        # Build feature dict based on model's expected features
        features = {}

        # Common feature extraction
        premium = float(flow.get("premium_usd") or 0)
        underlying = float(flow.get("underlying_price") or 0)
        strike = float(flow.get("strike") or 0)
        size = int(flow.get("size_contracts") or 0)
        volume = float(flow.get("volume_contract") or 0)
        oi = float(flow.get("open_interest") or 0)

        # Map flow fields to feature names used by pattern_miner
        feature_map = {
            "premium_usd": premium,
            "dte": int(flow.get("dte") or 0),
            "iv": float(flow.get("iv") or 0),
            "iv_rank_at_entry": float(flow.get("iv") or 0),  # Use IV as proxy
            "volume_contract": volume,
            "open_interest": oi,
            "underlying_price": underlying,
            "strike": strike,
            "size_contracts": size,
            "moneyness": strike / underlying if underlying > 0 else 1.0,
            "volume_oi_ratio": volume / oi if oi > 0 else 0,
            "premium_per_contract": premium / size if size > 0 else 0,
            # GEX/VEX features (may not be in flow, default to 0)
            "gex_at_entry": float(flow.get("gex") or 0),
            "vex_at_entry": float(flow.get("vex") or 0),
            "market_tide_30m": float(flow.get("market_tide") or 0),
            "max_pain_distance_pct": float(flow.get("max_pain_distance") or 0),
            "vix_at_entry": float(flow.get("vix") or 0),
            "darkpool_volume_1h": float(flow.get("darkpool_volume") or 0),
            # Categorical (encode as numbers)
            "put_call": 1 if flow.get("put_call") == "C" else 0,
            "vol_regime_at_entry": 0,
            "risk_regime_at_entry": 0,
            "session_regime_at_entry": 0,
            "trend_regime_at_entry": 0,
            "vix_regime_at_entry": 0,
            "market_tide_direction": 0,
        }

        # Return only the features the model expects
        if feature_names:
            for feat in feature_names:
                features[feat] = feature_map.get(feat, 0)
        else:
            return feature_map

        return features

    def score(self, flow: Dict[str, Any]) -> float:
        """
        Score a flow event. Returns probability [0, 1].

        Higher score = more likely to be a profitable trade.
        """
        # Determine trade bucket
        dte = flow.get("dte")
        if isinstance(dte, str):
            try:
                dte = int(dte)
            except ValueError:
                dte = None
        bucket = get_trade_bucket(dte)

        # Check if we have a model for this bucket
        if bucket not in self.models:
            return self._heuristic_score(flow)

        # Get model and features
        model_data = self.models[bucket]
        model = model_data.get("model")
        if model is None:
            return self._heuristic_score(flow)

        try:
            features = self.extract_features(flow, bucket)
            feature_names = self.feature_names[bucket]

            # Build feature vector in correct order
            feature_vector = np.array([[features.get(f, 0) for f in feature_names]])

            # Predict probability
            prob = model.predict_proba(feature_vector)[0][1]
            return float(prob)
        except Exception as e:
            logger.warning(f"Model scoring failed for bucket {bucket}: {e}")
            return self._heuristic_score(flow)

    def _heuristic_score(self, flow: Dict[str, Any]) -> float:
        """
        Heuristic baseline scorer when no trained model is available.

        Signals with high premium, sweeps, and good aggressor alignment
        get higher scores.
        """
        score = 0.3  # Base score

        # Premium factor (log scale)
        premium = float(flow.get("premium_usd") or 0)
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
        volume = float(flow.get("volume_contract") or 0)
        oi = float(flow.get("open_interest") or 1)
        vol_oi = volume / oi if oi > 0 else 0
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
        """Score multiple flows."""
        return [self.score(f) for f in flows]

    def get_loaded_models(self) -> List[str]:
        """Return list of loaded model types."""
        return list(self.models.keys())


# Singleton instance
_scorer: Optional[MLScorer] = None


def get_scorer() -> MLScorer:
    """Get or create the MLScorer singleton."""
    global _scorer
    if _scorer is None:
        _scorer = MLScorer()
    return _scorer


def reload_scorer() -> MLScorer:
    """Force reload of models (after pattern mining)."""
    global _scorer
    _scorer = MLScorer()
    return _scorer
