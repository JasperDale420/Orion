"""
ML Flow Processor.

Processes all flow events with ML scoring, bypassing rule pre-filters.
Generates CandidateTrades for flows that exceed the score threshold.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from orion.ml.scorer import get_scorer
from orion.shared.logger import setup_struct_logger
from orion.storage.models_gold import CandidateTrade, TradeDirection

logger = setup_struct_logger("orion.ml.flow_processor")

# Default score threshold (can be overridden by solver config)
DEFAULT_SCORE_THRESHOLD = 0.5


class MLFlowProcessor:
    """
    Processes flow events using pure ML scoring.

    Unlike RuleEngine which uses rule-based pre-filters, this processor
    scores every flow event and generates candidates based on ML probability.
    """

    def __init__(self, score_threshold: float = DEFAULT_SCORE_THRESHOLD) -> None:
        self.scorer = get_scorer()
        self.score_threshold = score_threshold
        logger.info(
            f"MLFlowProcessor initialized with threshold={score_threshold}",
            extra={"event": "processor_init", "threshold": score_threshold},
        )

    def process_flows(self, flows: List[Dict[str, Any]]) -> List[CandidateTrade]:
        """
        Score all flows and generate CandidateTrades for those above threshold.

        Args:
            flows: List of flow dicts (from SilverOptionFlow or raw payload)

        Returns:
            List of CandidateTrade objects for high-scoring flows
        """
        if not flows:
            return []

        candidates = []

        # Batch score all flows
        scores = self.scorer.score_batch(flows)

        for flow, score in zip(flows, scores):
            if score >= self.score_threshold:
                try:
                    candidate = self._flow_to_candidate(flow, score)
                    if candidate:
                        candidates.append(candidate)
                except Exception as e:
                    logger.warning(
                        f"Failed to create candidate from flow: {e}",
                        extra={"ticker": flow.get("ticker"), "score": score},
                    )

        logger.info(
            f"Processed {len(flows)} flows, generated {len(candidates)} candidates",
            extra={
                "event": "batch_processed",
                "total_flows": len(flows),
                "candidates_generated": len(candidates),
                "threshold": self.score_threshold,
            },
        )

        return candidates

    def _flow_to_candidate(self, flow: Dict[str, Any], score: float) -> Optional[CandidateTrade]:
        """Convert a high-scoring flow to a CandidateTrade."""
        ticker = flow.get("ticker")
        if not ticker:
            return None

        # Parse timestamp
        flow_ts = flow.get("flow_ts_utc")
        if isinstance(flow_ts, str):
            flow_ts = datetime.fromisoformat(flow_ts.replace("Z", "+00:00"))
        elif not isinstance(flow_ts, datetime):
            flow_ts = datetime.now(timezone.utc)

        # Determine direction from put/call and aggressor
        put_call = flow.get("put_call", "C")
        aggressor = flow.get("aggressor", "UNK")

        # ASK aggressor on calls = bullish, BID on puts = bullish
        # BID aggressor on calls = bearish, ASK on puts = bearish
        if (put_call == "C" and aggressor == "ASK") or (put_call == "P" and aggressor == "BID"):
            direction = TradeDirection.LONG
        elif (put_call == "C" and aggressor == "BID") or (put_call == "P" and aggressor == "ASK"):
            direction = TradeDirection.SHORT
        else:
            # Default to LONG for unclear aggressor
            direction = TradeDirection.LONG

        # Get rule matches for explainability (optional tagging)
        matched_rules = self._get_rule_tags(flow, score)

        candidate_id = f"ml_{uuid.uuid4().hex[:12]}"

        return CandidateTrade(
            candidate_id=candidate_id,
            ticker=ticker,
            timestamp_utc=flow_ts,
            direction=direction.value,  # Store as string
            rule_id="ml_score",  # ML-based, not rule-based
            confidence=score,
            source="ML_SCORER",
            evidence={
                "ml_score": score,
                "premium_usd": float(flow.get("premium_usd") or 0),
                "is_sweep": str(flow.get("is_sweep", "")).lower() == "true",
                "aggressor": aggressor,
                "put_call": put_call,
                "dte": int(flow.get("dte") or 0) if flow.get("dte") else None,
                "matched_rules": matched_rules,
                "underlying_price": float(flow.get("underlying_price") or 0),
                "option_price": float(flow.get("option_price") or 0),
            },
        )

    def _get_rule_tags(self, flow: Dict[str, Any], score: float) -> List[str]:
        """
        Get explainability tags based on flow characteristics.
        These are not pre-filters, just labels for understanding why ML scored high.
        """
        tags = []

        premium = float(flow.get("premium_usd") or 0)
        is_sweep = str(flow.get("is_sweep", "")).lower() == "true"
        dte = flow.get("dte")

        if premium >= 500000:
            tags.append("whale_premium")
        elif premium >= 100000:
            tags.append("high_premium")

        if is_sweep:
            tags.append("sweep")

        if dte is not None:
            if dte == 0:
                tags.append("0dte")
            elif dte <= 3:
                tags.append("short_swing")
            elif dte <= 14:
                tags.append("swing")
            else:
                tags.append("position")

        vol_oi = flow.get("volume_oi_ratio")
        if vol_oi and float(vol_oi) > 2.0:
            tags.append("unusual_volume")

        return tags


def process_flows_with_ml(
    flows: List[Dict[str, Any]],
    threshold: float = DEFAULT_SCORE_THRESHOLD,
) -> List[CandidateTrade]:
    """
    Convenience function to process flows with ML scoring.
    """
    processor = MLFlowProcessor(score_threshold=threshold)
    return processor.process_flows(flows)
