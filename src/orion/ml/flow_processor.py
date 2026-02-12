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

        NOTE: This method uses raw flow data without enrichment. For full feature
        parity with training data, use process_flows_enriched() instead.

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

        for flow, score in zip(flows, scores, strict=True):
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

    async def process_flows_enriched(self, flows: List[Dict[str, Any]]) -> List[CandidateTrade]:
        """
        Score all flows with full feature enrichment.

        This method enriches each flow with database features (GEX, market tide,
        regimes, etc.) before scoring, ensuring feature parity with training data.

        Args:
            flows: List of flow dicts

        Returns:
            List of CandidateTrade objects for high-scoring flows
        """
        import asyncio

        if not flows:
            return []

        candidates = []

        # Score each flow with enrichment (async)
        async def score_flow(flow: Dict[str, Any]) -> tuple:
            score = await self.scorer.score_enriched(flow)
            return flow, score

        # Run enrichment in parallel
        results = await asyncio.gather(*[score_flow(f) for f in flows], return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Flow scoring failed: {result}")
                continue

            flow, score = result
            if score >= self.score_threshold:
                try:
                    candidate = self._flow_to_candidate(flow, score)
                    if candidate:
                        candidates.append(candidate)
                        logger.info(
                            f"High-scoring candidate: {flow.get('ticker')} score={score:.3f}",
                            extra={
                                "event": "enriched_candidate",
                                "ticker": flow.get("ticker"),
                                "score": score,
                                "premium_usd": flow.get("premium_usd"),
                            },
                        )
                except Exception as e:
                    logger.warning(
                        f"Failed to create candidate from flow: {e}",
                        extra={"ticker": flow.get("ticker"), "score": score},
                    )

        logger.info(
            f"Processed {len(flows)} flows with enrichment, generated {len(candidates)} candidates",
            extra={
                "event": "batch_processed_enriched",
                "total_flows": len(flows),
                "candidates_generated": len(candidates),
                "threshold": self.score_threshold,
            },
        )

        return candidates

    def _flow_to_candidate(self, flow: Dict[str, Any], score: float) -> Optional[CandidateTrade]:
        """Convert a high-scoring flow to a CandidateTrade with options contract details."""
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

        # Extract options contract details
        strike_price = float(flow.get("strike") or 0) if flow.get("strike") else None
        option_price = float(flow.get("option_price") or 0) if flow.get("option_price") else None
        underlying_price = float(flow.get("underlying_price") or 0) if flow.get("underlying_price") else None
        premium_usd = float(flow.get("premium_usd") or 0) if flow.get("premium_usd") else None

        # Parse expiration date
        expiration_date = None
        expiry_str = flow.get("expiry")
        if expiry_str:
            try:
                expiration_date = datetime.strptime(expiry_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                logger.warning(f"Failed to parse expiry date: {expiry_str}")

        # Option type: CALL or PUT
        option_type = "CALL" if put_call == "C" else "PUT"

        # Get OCC symbol - use option_chain if available, otherwise generate
        option_symbol = flow.get("option_chain")
        if not option_symbol and strike_price and expiration_date:
            option_symbol = self._generate_occ_symbol(ticker, expiration_date, option_type, strike_price)

        return CandidateTrade(
            candidate_id=candidate_id,
            ticker=ticker,
            timestamp_utc=flow_ts,
            direction=direction.value,  # Store as string
            rule_id="ml_score",  # ML-based, not rule-based
            confidence=score,
            source="UW",  # Changed from ML_SCORER to UW for SolverRouter
            # Options contract details
            option_symbol=option_symbol,
            strike_price=strike_price,
            expiration_date=expiration_date,
            option_type=option_type,
            underlying_price=underlying_price,
            premium=option_price,  # Per-contract premium
            evidence={
                "ml_score": score,
                "premium_usd": premium_usd or 0,
                "is_sweep": str(flow.get("is_sweep", "")).lower() == "true",
                "aggressor": aggressor,
                "put_call": put_call,
                "dte": int(flow.get("dte") or 0) if flow.get("dte") else None,
                "matched_rules": matched_rules,
                "underlying_price": underlying_price or 0,
                "option_price": option_price or 0,
                "strike": strike_price,
                "expiry": expiry_str,
            },
        )

    def _generate_occ_symbol(
        self, ticker: str, expiration_date: datetime, option_type: str, strike_price: float
    ) -> str:
        """
        Generate OCC-format option symbol.

        Format: SYMBOL + YYMMDD + C/P + STRIKE*1000 (8 digits, zero-padded)
        Example: AAPL240419C00190000 = AAPL April 19, 2024 $190 Call
        """
        date_str = expiration_date.strftime("%y%m%d")
        opt_type = "C" if option_type.upper() == "CALL" else "P"
        strike_int = int(strike_price * 1000)
        strike_str = f"{strike_int:08d}"
        return f"{ticker.upper()}{date_str}{opt_type}{strike_str}"

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
