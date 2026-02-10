"""
ML Scorer for flow events.

Scores every flow event with a trained LightGBM model.
Supports bucket-specific models (0DTE, SHORT_SWING, SWING, POSITION).
"""

import pickle
from typing import Any, Dict, List, Optional

import numpy as np

from orion.config import system_settings
from orion.shared.logger import setup_struct_logger

logger = setup_struct_logger("orion.ml.scorer")

# Default model path
MODEL_DIR = system_settings.model_dir

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

# All available targets (matches pattern_miner.py TARGETS)
ALL_TARGETS = ["hit_target_50", "avoid_stop", "hit_target_100", "quick_winner"]


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
        # Backward compatibility fields expected by older tests/callers.
        self.model: Any | None = None
        self.use_heuristic: bool = True

        self._load_models()
        self.use_heuristic = len(self.models) == 0
        self.model = self.models.get("SWING", {}).get("model")

    def _load_models(self) -> None:
        """Load all available bucket-specific models with freshness validation."""
        if not MODEL_DIR.exists():
            logger.info(
                f"Model directory {MODEL_DIR} does not exist, using heuristic scorer",
                extra={"event": "using_heuristic"},
            )
            return

        # Model freshness config (envvar override)
        max_age_days = system_settings.max_model_age_days

        loaded_count = 0
        skipped_stale = 0
        for bucket in TRADE_BUCKETS:
            model_type = f"{bucket}_{self.target}"
            model_path = MODEL_DIR / f"{model_type}.pkl"

            if model_path.exists():
                # Check model freshness before loading
                from datetime import datetime

                model_mtime = datetime.fromtimestamp(model_path.stat().st_mtime)
                model_age_days = (datetime.now() - model_mtime).days

                if model_age_days > max_age_days:
                    logger.warning(
                        f"Model {model_type} is {model_age_days} days old (limit: {max_age_days}), skipping",
                        extra={
                            "event": "stale_model_skipped",
                            "model_type": model_type,
                            "age_days": model_age_days,
                            "max_age_days": max_age_days,
                        },
                    )
                    skipped_stale += 1
                    continue

                try:
                    with open(model_path, "rb") as f:
                        model_data = pickle.load(f)

                    self.models[bucket] = model_data
                    self.feature_names[bucket] = model_data.get("feature_names", [])
                    loaded_count += 1

                    logger.info(
                        f"Loaded model {model_type} (age: {model_age_days}d)",
                        extra={
                            "event": "model_loaded",
                            "model_type": model_type,
                            "path": str(model_path),
                            "age_days": model_age_days,
                        },
                    )
                except Exception as e:
                    logger.warning(f"Failed to load model {model_type}: {e}")

        if loaded_count == 0:
            logger.info("No bucket models found, using heuristic scorer")
        else:
            summary = f"Loaded {loaded_count}/{len(TRADE_BUCKETS)} bucket models"
            if skipped_stale > 0:
                summary += f" (skipped {skipped_stale} stale)"
            logger.info(
                summary,
                extra={"event": "models_loaded", "count": loaded_count, "stale_skipped": skipped_stale},
            )

    def extract_features(self, flow: Dict[str, Any], bucket: Optional[str] = None) -> Dict[str, float]:
        """
        Extract features from a flow event for scoring.
        Uses feature names from the model if available.
        """
        if bucket is None:
            dte = flow.get("dte")
            if isinstance(dte, str):
                try:
                    dte = int(dte)
                except ValueError:
                    dte = None
            bucket = get_trade_bucket(dte)

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

            # Apply confidence rules (post-model adjustments)
            adjusted_prob = self._apply_confidence_rules(float(prob), flow, bucket)
            return adjusted_prob
        except Exception as e:
            logger.warning(f"Model scoring failed for bucket {bucket}: {e}")
            return self._heuristic_score(flow)

    def _heuristic_score(self, flow: Dict[str, Any]) -> float:
        """
        Heuristic baseline scorer when no trained model is available.

        Signals with high premium, sweeps, and good aggressor alignment
        get higher scores. CAPPED at 0.50 to prevent heuristic from
        reaching live threshold (0.70) - models are required for live trading.
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

        # In live mode we cap heuristic output to avoid taking untrained-model signals.
        # In paper/backtest, keep uncapped behavior for compatibility/analysis.
        raw_score = min(max(score, 0.0), 1.0)
        cap_enabled = system_settings.orion_stage == "live"
        capped_score = min(raw_score, 0.50) if cap_enabled else raw_score

        if cap_enabled and raw_score > 0.50:
            logger.warning(
                f"Heuristic scorer used (no model) - score capped from {raw_score:.2f} to {capped_score:.2f}",
                extra={"event": "heuristic_scorer_capped", "raw_score": raw_score, "capped_score": capped_score},
            )

        return capped_score

    def _apply_confidence_rules(self, base_score: float, flow: Dict[str, Any], bucket: str) -> float:
        """
        Apply post-model confidence rules.

        These rules adjust the model's probability score based on patterns
        discovered by the EOD agent and pattern miner.

        Current rules:
        - 0dte_low_gex_vex_avoid_stop: For 0DTE trades, when GEX and VEX are
          both below their historical averages, increase confidence by 1.3x.
          This rule predicts stop avoidance with 100% accuracy (n=149).
        """
        adjusted_score = base_score

        # Rule: 0DTE low GEX/VEX avoid stop (EOD agent proposal 2026-01-09)
        # When GEX and VEX are both below average, the trade is more likely
        # to avoid hitting stop loss (AUC=0.75, hit_rate=100%)
        if bucket == "0DTE":
            gex = flow.get("gex_at_entry") or flow.get("gex")
            vex = flow.get("vex_at_entry") or flow.get("vex")

            # Use rolling averages from flow or fall back to hardcoded baseline
            # These averages should be updated periodically from silver_greek_exposure
            gex_avg = flow.get("gex_rolling_avg", 0)  # If not present, rule won't apply
            vex_avg = flow.get("vex_rolling_avg", 0)

            # Only apply if we have valid GEX/VEX data
            if gex is not None and vex is not None and gex_avg != 0 and vex_avg != 0:
                if gex < gex_avg and vex < vex_avg:
                    multiplier = 1.3
                    adjusted_score = min(base_score * multiplier, 1.0)  # Cap at 1.0
                    logger.info(
                        f"0DTE low GEX/VEX rule applied: {base_score:.3f} → {adjusted_score:.3f}",
                        extra={
                            "event": "confidence_rule_applied",
                            "rule": "0dte_low_gex_vex_avoid_stop",
                            "gex": gex,
                            "vex": vex,
                            "gex_avg": gex_avg,
                            "vex_avg": vex_avg,
                            "multiplier": multiplier,
                        },
                    )

        return adjusted_score

    def should_trade(self, flow: Dict[str, Any], threshold: float = DEFAULT_SCORE_THRESHOLD) -> bool:
        """Check if flow score exceeds threshold."""
        return self.score(flow) >= threshold

    async def score_enriched(self, flow: Dict[str, Any]) -> float:
        """
        Score a flow with full feature enrichment.

        This method enriches the flow with all features from the database
        (GEX, market tide, regimes, Greeks, etc.) before scoring.
        This ensures feature parity between training and inference.

        Use this for real-time scoring where flow data is incomplete.
        """
        from datetime import datetime, timezone

        from orion.ml.flow_enricher import enrich_flow_for_scoring

        # Extract basic flow info
        ticker = flow.get("ticker", "")
        entry_ts = flow.get("timestamp_utc") or flow.get("flow_ts_utc") or datetime.now(timezone.utc)
        if isinstance(entry_ts, str):
            from dateutil.parser import parse

            entry_ts = parse(entry_ts)

        put_call = flow.get("put_call", "C")
        strike = flow.get("strike") or flow.get("strike_price")
        underlying = flow.get("underlying_price")
        dte = flow.get("dte")
        premium = flow.get("premium_usd")
        event_id = flow.get("event_id")
        option_chain = flow.get("option_chain") or flow.get("option_symbol")
        aggressor = flow.get("aggressor")
        is_sweep = flow.get("is_sweep") or flow.get("is_sweep") == "true"
        expiry = flow.get("expiry")  # Pass expiry for DTE calculation

        try:
            # Enrich with all database features
            enriched = await enrich_flow_for_scoring(
                ticker=ticker,
                entry_ts=entry_ts,
                put_call=put_call,
                strike=strike,
                underlying_price=underlying,
                dte=dte,
                premium_usd=premium,
                event_id=event_id,
                option_chain=option_chain,
                aggressor=aggressor,
                is_sweep=is_sweep,
                expiry=expiry,  # Pass expiry for DTE calculation
            )

            logger.debug(
                f"Enriched flow for {ticker}: {sum(1 for v in enriched.values() if v is not None)} non-null features",
                extra={"event": "flow_enriched", "ticker": ticker},
            )

            # Score with enriched features
            return self.score(enriched)
        except Exception as e:
            logger.warning(f"Flow enrichment failed for {ticker}: {e}, using raw features")
            return self.score(flow)

    def score_batch(self, flows: List[Dict[str, Any]]) -> List[float]:
        """Score multiple flows."""
        return [self.score(f) for f in flows]

    def get_loaded_models(self) -> List[str]:
        """Return list of loaded model types."""
        return list(self.models.keys())


class MultiTargetScorer:
    """
    Scores flow events across all available targets.

    Provides comprehensive scoring:
    - hit_target_50: Probability of 50% profit before 20% stop
    - avoid_stop: Probability of avoiding 20% stop entirely
    - hit_target_100: Probability of 100% profit (high conviction runner)
    - quick_winner: Probability of 50% profit within 1 hour (fast exit)
    """

    def __init__(self) -> None:
        self.scorers: Dict[str, MLScorer] = {}
        for target in ALL_TARGETS:
            self.scorers[target] = MLScorer(target=target)

        logger.info(
            f"MultiTargetScorer initialized with {len(self.scorers)} targets",
            extra={"event": "multi_scorer_init", "targets": ALL_TARGETS},
        )

    def score_all(self, flow: Dict[str, Any]) -> Dict[str, float]:
        """
        Score a flow event across all targets.

        Returns:
            Dict mapping target name to probability [0, 1].
            Example: {"hit_target_50": 0.72, "avoid_stop": 0.85, ...}
        """
        scores = {}
        for target, scorer in self.scorers.items():
            scores[target] = scorer.score(flow)
        return scores

    def get_composite_score(self, flow: Dict[str, Any], weights: Optional[Dict[str, float]] = None) -> float:
        """
        Calculate a weighted composite score across all targets.

        Default weights favor profit targets over risk avoidance.
        """
        default_weights = {
            "hit_target_50": 0.35,
            "avoid_stop": 0.25,
            "hit_target_100": 0.25,
            "quick_winner": 0.15,
        }
        w = weights or default_weights

        scores = self.score_all(flow)
        composite = sum(scores.get(t, 0) * w.get(t, 0) for t in ALL_TARGETS)
        return composite

    def get_trade_signal(self, flow: Dict[str, Any], thresholds: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """
        Generate a comprehensive trade signal with all target scores.

        Returns:
            Dict with scores, composite score, and recommendation.
        """
        default_thresholds = {
            "hit_target_50": 0.55,
            "avoid_stop": 0.60,
            "hit_target_100": 0.50,
            "quick_winner": 0.45,
        }
        t = thresholds or default_thresholds

        scores = self.score_all(flow)
        composite = self.get_composite_score(flow)

        # Determine recommendation
        passing_targets = [target for target, score in scores.items() if score >= t.get(target, 0.5)]

        if len(passing_targets) >= 3:
            recommendation = "STRONG_BUY"
        elif len(passing_targets) >= 2:
            recommendation = "BUY"
        elif scores.get("avoid_stop", 0) < 0.4:
            recommendation = "AVOID"  # High risk of stop-out
        else:
            recommendation = "NEUTRAL"

        return {
            "scores": scores,
            "composite_score": composite,
            "passing_targets": passing_targets,
            "recommendation": recommendation,
        }


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
