"""
ML Scorer for flow events.

Scores every flow event with a trained LightGBM model.
Supports bucket-specific models (0DTE, SHORT_SWING, SWING, POSITION).
"""

import pickle
import time
from datetime import UTC
from typing import Any

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


def get_trade_bucket(dte: int | None) -> str:
    """Classify a flow into trade bucket based on DTE."""
    if dte is None:
        return "SWING"
    if dte <= 0:
        return "0DTE"
    if dte <= 3:
        return "SHORT_SWING"
    if dte <= 14:
        return "SWING"
    return "POSITION"


def _parse_dte_and_bucket(flow: dict[str, Any]) -> tuple[int | None, str]:
    dte = flow.get("dte")
    if isinstance(dte, str):
        try:
            dte = int(dte)
        except ValueError:
            dte = None
    return dte, get_trade_bucket(dte)


class MLScorer:
    """
    Scores flow events using trained LightGBM models.

    Loads bucket-specific models (e.g., SWING_hit_target_50.pkl) trained
    by pattern_miner.py. Falls back to heuristic scorer when no model exists.
    """

    # How often to check disk for updated model files (seconds)
    _RELOAD_CHECK_INTERVAL: float = 60.0

    def __init__(self, target: str = DEFAULT_TARGET) -> None:
        self.target = target
        self.models: dict[str, Any] = {}  # bucket -> model_data
        self.feature_names: dict[str, list[str]] = {}  # bucket -> feature names
        self._model_mtimes: dict[str, float] = {}  # bucket -> last loaded mtime
        self._last_reload_check: float = time.monotonic()
        # Backward compatibility fields expected by older tests/callers.
        self.model: Any | None = None
        self.use_heuristic: bool = True
        self._bypass_scoring: bool = False

        self._load_models()
        self.use_heuristic = len(self.models) == 0
        self.model = self.models.get("SWING", {}).get("model")

    def _load_models(self) -> None:
        """Load all available bucket-specific models with freshness validation.

        Respects ``ORION_ML_STALE_MODEL_POLICY``:
        - ``skip``: reject models older than max_model_age_days (original behaviour)
        - ``warn``: load stale models but log a warning
        - ``bypass``: do not load any models; ML scoring is skipped entirely
        """
        stale_policy = system_settings.ml_stale_model_policy

        if stale_policy == "bypass":
            logger.warning(
                "ML stale model policy is 'bypass' — ML scoring disabled, candidates pass through",
                extra={"event": "ml_scoring_bypassed", "policy": stale_policy},
            )
            self._bypass_scoring = True
            return

        self._bypass_scoring = False

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
        loaded_stale = 0
        for bucket in TRADE_BUCKETS:
            model_type = f"{bucket}_{self.target}"
            model_path = MODEL_DIR / f"{model_type}.pkl"

            if model_path.exists():
                # Check model freshness before loading
                from datetime import datetime

                model_mtime = datetime.fromtimestamp(model_path.stat().st_mtime, tz=UTC)
                model_age_days = (datetime.now(UTC) - model_mtime).days
                is_stale = model_age_days > max_age_days

                if is_stale and stale_policy == "skip":
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

                if is_stale and stale_policy == "warn":
                    logger.warning(
                        f"Model {model_type} is {model_age_days} days old (limit: {max_age_days}), "
                        f"loading anyway per stale_model_policy='warn'",
                        extra={
                            "event": "stale_model_loaded_warn",
                            "model_type": model_type,
                            "age_days": model_age_days,
                            "max_age_days": max_age_days,
                            "policy": stale_policy,
                        },
                    )

                try:
                    with open(model_path, "rb") as f:
                        model_data = pickle.load(f)  # noqa: S301

                    self.models[bucket] = model_data
                    self.feature_names[bucket] = model_data.get("feature_names", [])
                    self._model_mtimes[bucket] = model_path.stat().st_mtime
                    loaded_count += 1
                    if is_stale:
                        loaded_stale += 1

                    logger.info(
                        f"Loaded model {model_type} (age: {model_age_days}d{', STALE' if is_stale else ''})",
                        extra={
                            "event": "model_loaded",
                            "model_type": model_type,
                            "path": str(model_path),
                            "age_days": model_age_days,
                            "is_stale": is_stale,
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
            if loaded_stale > 0:
                summary += f" ({loaded_stale} stale, policy={stale_policy})"
            logger.info(
                summary,
                extra={
                    "event": "models_loaded",
                    "count": loaded_count,
                    "stale_skipped": skipped_stale,
                    "stale_loaded": loaded_stale,
                },
            )

    def check_and_reload(self) -> bool:
        """Reload models if any .pkl file on disk is newer than what we loaded."""
        if not MODEL_DIR.exists():
            return False
        for bucket in TRADE_BUCKETS:
            model_path = MODEL_DIR / f"{bucket}_{self.target}.pkl"
            if model_path.exists():
                disk_mtime = model_path.stat().st_mtime
                loaded_mtime = self._model_mtimes.get(bucket, 0)
                if disk_mtime > loaded_mtime:
                    logger.info("model_files_changed_reloading", bucket=bucket, target=self.target)
                    self._load_models()
                    self.use_heuristic = len(self.models) == 0
                    self.model = self.models.get("SWING", {}).get("model")
                    return True
        return False

    def _maybe_reload(self) -> None:
        """Check for updated model files at most once every _RELOAD_CHECK_INTERVAL seconds."""
        now = time.monotonic()
        if now - self._last_reload_check >= self._RELOAD_CHECK_INTERVAL:
            self._last_reload_check = now
            self.check_and_reload()

    @staticmethod
    def _build_feature_map(flow: dict[str, Any]) -> dict[str, float]:
        """Build the full mapping from flow fields to ML feature names."""
        import math
        from datetime import datetime, timezone

        get = lambda k: float(flow.get(k) or 0)  # noqa: E731
        premium = get("premium_usd")
        underlying = get("underlying_price")
        strike = get("strike")
        size = int(flow.get("size_contracts") or 0)
        volume = get("volume_contract")
        oi = get("open_interest")
        dte_val = int(flow.get("dte") or 0)
        iv_val = get("iv")
        moneyness_val = strike / underlying if underlying > 0 else 1.0
        log_moneyness_val = math.log(moneyness_val) if moneyness_val > 0 else 0.0
        delta_val = get("delta")
        gamma_val = get("gamma")
        theta_val = get("theta")
        vega_val = get("vega")
        iv_rank_val = get("iv_rank") or iv_val

        now = datetime.now(timezone.utc)
        hour_of_day = now.hour
        minute_of_hour = now.minute
        day_of_week = now.weekday()
        market_open_minutes = 9 * 60 + 30
        current_minutes = hour_of_day * 60 + minute_of_hour
        minutes_since_open = max(current_minutes - market_open_minutes, 0)
        minutes_to_close = max(16 * 60 - current_minutes, 0)

        safe = lambda n, d, fallback=0: n / d if d > 0 else fallback  # noqa: E731

        return {
            "premium_usd": premium,
            "premium": premium,  # training alias
            "dte": dte_val,
            "days_to_expiry": dte_val,  # training alias
            "iv": iv_val,
            "iv_rank_at_entry": iv_rank_val,
            "iv_rank": iv_rank_val,  # training alias
            "volume_contract": volume,
            "volume": volume,  # training alias
            "open_interest": oi,
            "underlying_price": underlying,
            "spot_price": underlying,  # training alias
            "contract_price": safe(premium, size),  # training alias (best estimate)
            "strike": strike,
            "size_contracts": size,
            "moneyness": moneyness_val,
            "log_moneyness": log_moneyness_val,  # training alias
            "volume_oi_ratio": safe(volume, oi),
            "premium_per_contract": safe(premium, size),
            "delta": delta_val,  # training alias
            "gamma": gamma_val,  # training alias
            "theta": theta_val,  # training alias
            "vega": vega_val,  # training alias
            "underlying_30d_return": get("underlying_30d_return"),
            "underlying_5d_return": get("underlying_5d_return"),
            "underlying_1d_return": get("underlying_1d_return"),
            "realized_vol_20d": get("realized_vol_20d"),
            "hour_of_day": hour_of_day,
            "minute_of_hour": minute_of_hour,
            "day_of_week": day_of_week,
            "minutes_since_open": minutes_since_open,
            "minutes_to_close": minutes_to_close,
            "gex_at_entry": get("gex"),
            "vex_at_entry": get("vex"),
            "market_tide_30m": get("market_tide"),
            "max_pain_distance_pct": get("max_pain_distance"),
            "vix_at_entry": get("vix"),
            "darkpool_volume_1h": get("darkpool_volume"),
            "put_call": 1 if flow.get("put_call") == "C" else 0,
            "alert_type": get("alert_type"),
            "side": get("side"),
            "aggressor": get("aggressor"),
            "is_bullish": get("is_bullish"),
            "is_bearish": get("is_bearish"),
            "is_sweep": get("is_sweep"),
            "is_block": get("is_block"),
            "is_unusual": get("is_unusual"),
            "vol_regime_at_entry": 0,
            "risk_regime_at_entry": 0,
            "session_regime_at_entry": 0,
            "trend_regime_at_entry": 0,
            "vix_regime_at_entry": 0,
            "market_tide_direction": 0,
            # Equity-level Gold features (populated by feature_store)
            # Momentum features
            "momentum_1d": get("momentum_1d"),
            "momentum_5d": get("momentum_5d"),
            "momentum_10d": get("momentum_10d"),
            "momentum_20d": get("momentum_20d"),
            "rsi_14": get("rsi_14"),
            "rsi_28": get("rsi_28"),
            "macd": get("macd"),
            "macd_signal": get("macd_signal"),
            # Volatility features
            "vol_5d": get("vol_5d"),
            "vol_20d": get("vol_20d"),
            "vol_ratio_5_20": get("vol_ratio_5_20"),
            "atr_14": get("atr_14"),
            "bb_width_20": get("bb_width_20"),
            "price_zscore_20d": get("price_zscore_20d"),
            # Flow features
            "total_premium_24h": get("total_premium_24h"),
            "call_put_premium_ratio": get("call_put_premium_ratio"),
            "net_premium_24h": get("net_premium_24h"),
            "sweep_count_24h": get("sweep_count_24h"),
            "net_bull_premium_lr": get("net_bull_premium_lr"),
            "sweep_volume_share": get("sweep_volume_share"),
            # Temporal excursion features (from Gold temporal_excursion_features)
            "time_to_mfe_seconds": get("time_to_mfe_seconds"),
            "time_to_mae_seconds": get("time_to_mae_seconds"),
            "mfe_mae_ratio": get("mfe_mae_ratio"),
            "excursion_velocity": get("excursion_velocity"),
            "capture_efficiency": get("capture_efficiency"),
        }

    def extract_features(self, flow: dict[str, Any], bucket: str | None = None) -> dict[str, float]:
        """Extract features from a flow event for scoring.

        Uses feature names from the model if available.
        """
        if bucket is None:
            _, bucket = _parse_dte_and_bucket(flow)

        feature_names = self.feature_names.get(bucket, [])
        feature_map = self._build_feature_map(flow)

        if not feature_names:
            return feature_map

        return {feat: feature_map.get(feat, 0) for feat in feature_names}

    @property
    def bypass_scoring(self) -> bool:
        """True when ML scoring is entirely bypassed (policy='bypass')."""
        return self._bypass_scoring

    def score(self, flow: dict[str, Any]) -> float:
        """
        Score a flow event. Returns probability [0, 1].

        Higher score = more likely to be a profitable trade.
        """
        self._maybe_reload()

        if self._bypass_scoring:
            return -1.0  # Sentinel: caller should skip ML gating

        # Determine trade bucket
        _, bucket = _parse_dte_and_bucket(flow)

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

    def _heuristic_score(self, flow: dict[str, Any]) -> float:
        """
        Heuristic baseline scorer when no trained model is available.

        Signals with high premium, sweeps, and good aggressor alignment
        get higher scores. CAPPED at 0.50 to prevent heuristic from
        reaching live threshold (0.70) - models are required for live trading.

        Weights are configurable via the HeuristicWeights config
        (env prefix ORION_HEURISTIC_).
        """
        from orion.config import heuristic_weights as hw

        score = hw.base_score

        # Premium factor (log scale)
        premium = float(flow.get("premium_usd") or 0)
        if premium >= 500000:
            score += hw.premium_500k_plus
        elif premium >= 100000:
            score += hw.premium_100k_plus
        elif premium >= 50000:
            score += hw.premium_50k_plus
        elif premium >= 25000:
            score += hw.premium_25k_plus

        # Sweep bonus
        is_sweep = str(flow.get("is_sweep", "")).lower() == "true"
        if is_sweep:
            score += hw.sweep_bonus

        # Aggressor alignment (ASK = bullish intent for calls)
        aggressor = flow.get("aggressor", "")
        put_call = flow.get("put_call", "")
        if (put_call == "C" and aggressor == "ASK") or (put_call == "P" and aggressor == "BID"):
            score += hw.ask_side_bonus

        # Volume/OI ratio (unusual activity)
        volume = float(flow.get("volume_contract") or 0)
        oi = float(flow.get("open_interest") or 1)
        vol_oi = volume / oi if oi > 0 else 0
        if vol_oi > 2.0:
            score += hw.vol_oi_high
        elif vol_oi > 1.0:
            score += hw.vol_oi_moderate

        # Penalize very low premium (noise)
        if premium < 10000:
            score += hw.low_premium_penalty

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

    def _apply_confidence_rules(self, base_score: float, flow: dict[str, Any], bucket: str) -> float:
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

    def should_trade(self, flow: dict[str, Any], threshold: float = DEFAULT_SCORE_THRESHOLD) -> bool:
        """Check if flow score exceeds threshold."""
        return self.score(flow) >= threshold

    async def score_enriched(self, flow: dict[str, Any]) -> float:
        """Score a flow using pre-computed features from Heber Gold.

        Reads the same Gold datasets used for training (meta_label_features +
        equity Gold), ensuring zero train/inference skew by construction.

        Falls back to raw flow fields if Gold lookup fails.
        """
        self._maybe_reload()

        from datetime import datetime

        from orion.ml.feature_store import get_scoring_features

        ticker = flow.get("ticker", "")
        entry_ts = flow.get("timestamp_utc") or flow.get("flow_ts_utc") or datetime.now(UTC)
        if isinstance(entry_ts, str):
            from dateutil.parser import parse

            entry_ts = parse(entry_ts)

        event_id = flow.get("event_id") or ""

        try:
            gold_features = await get_scoring_features(
                event_id=event_id,
                ticker=ticker,
                entry_ts=entry_ts,
            )

            # Merge: flow's own fields as base, Gold features overlay
            merged = dict(flow)
            for key, val in gold_features.items():
                if val is not None:
                    merged[key] = val

            non_null = sum(1 for v in gold_features.values() if v is not None)
            logger.debug(
                f"Loaded {non_null} Gold features for {ticker}",
                extra={"event": "feature_store_loaded", "ticker": ticker, "feature_count": non_null},
            )

            return self.score(merged)
        except Exception as e:
            logger.warning(f"Feature store lookup failed for {ticker}: {e}, using raw features")
            return self.score(flow)

    def score_batch(self, flows: list[dict[str, Any]]) -> list[float]:
        """Score multiple flows."""
        return [self.score(f) for f in flows]

    def get_loaded_models(self) -> list[str]:
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
        self.scorers: dict[str, MLScorer] = {}
        for target in ALL_TARGETS:
            self.scorers[target] = MLScorer(target=target)

        logger.info(
            f"MultiTargetScorer initialized with {len(self.scorers)} targets",
            extra={"event": "multi_scorer_init", "targets": ALL_TARGETS},
        )

    def score_all(self, flow: dict[str, Any]) -> dict[str, float]:
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

    def get_composite_score(self, flow: dict[str, Any], weights: dict[str, float] | None = None) -> float:
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

    def get_trade_signal(self, flow: dict[str, Any], thresholds: dict[str, float] | None = None) -> dict[str, Any]:
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
_scorer: MLScorer | None = None


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
