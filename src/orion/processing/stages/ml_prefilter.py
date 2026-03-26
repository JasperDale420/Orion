"""
MLPreFilter Pipeline Stage.

Rejects low-probability candidates before expensive solver evaluation.
"""

from __future__ import annotations

from typing import Any

from orion.config import system_settings
from orion.processing.pipeline import PipelineContext, StageResult
from orion.shared.logger import setup_struct_logger
from orion.storage.models_gold import CandidateTrade

logger = setup_struct_logger("orion.processing.stages.ml_prefilter")


class MLPreFilter:
    """Score candidates with ML model and reject below threshold."""

    @property
    def name(self) -> str:
        return "ml_prefilter"

    @staticmethod
    def _normalize_put_call(option_type: Any) -> str | None:
        value = str(option_type or "").upper()
        if value in {"C", "CALL"}:
            return "C"
        if value in {"P", "PUT"}:
            return "P"
        return None

    @classmethod
    def _build_payload(cls, candidate: CandidateTrade) -> dict[str, Any]:
        evidence = candidate.evidence or {}
        execution_params = candidate.execution_params or {}

        put_call = cls._normalize_put_call(candidate.option_type) or cls._normalize_put_call(
            evidence.get("put_call") or execution_params.get("put_call")
        )

        premium_usd = evidence.get("premium_usd")
        if premium_usd in (None, "", 0):
            premium_usd = candidate.premium

        dte = None
        if candidate.expiration_date:
            try:
                dte = (candidate.expiration_date - candidate.timestamp_utc).days
                if dte < 0:
                    dte = 0
            except Exception:
                dte = None
                logger.debug("DTE calculation failed", extra={"ticker": candidate.ticker}, exc_info=True)

        payload = {
            "ticker": candidate.ticker,
            "option_symbol": candidate.option_symbol,
            "premium_usd": premium_usd,
            "dte": dte,
            "put_call": put_call,
            "strike": candidate.strike_price,
            "underlying_price": candidate.underlying_price,
            "timestamp_utc": candidate.timestamp_utc,
            "event_id": evidence.get("event_id"),
            "aggressor": evidence.get("aggressor"),
            "is_sweep": evidence.get("is_sweep"),
            "expiry": candidate.expiration_date.date().isoformat() if candidate.expiration_date else None,
        }
        for key, value in execution_params.items():
            if key not in payload:
                payload[key] = value
        return payload

    @staticmethod
    def _has_minimum_context(flow_dict: dict[str, Any]) -> bool:
        premium = flow_dict.get("premium_usd")
        put_call = flow_dict.get("put_call")
        try:
            premium_value = float(premium)
        except (TypeError, ValueError):
            return False
        return bool(premium_value > 0 and put_call in {"C", "P"})

    async def evaluate(self, ctx: PipelineContext) -> StageResult:
        candidate = ctx.candidate

        try:
            from orion.ml.scorer import get_scorer

            flow_dict = self._build_payload(candidate)
            if not self._has_minimum_context(flow_dict):
                logger.debug(
                    "Skipping ML pre-filter due to incomplete candidate context",
                    extra={"event": "ml_prefilter_bypass_incomplete", "ticker": candidate.ticker},
                )
                return StageResult(
                    action="CONTINUE",
                    trace={"ml_prefilter": "bypassed_incomplete_context"},
                )

            scorer = get_scorer()

            # Bypass mode: ML scoring entirely disabled (stale model policy='bypass')
            if scorer.bypass_scoring:
                logger.warning(
                    f"ML pre-filter bypassed for {candidate.ticker} (stale_model_policy=bypass)",
                    extra={"event": "ml_prefilter_bypass_policy", "ticker": candidate.ticker},
                )
                return StageResult(
                    action="CONTINUE",
                    trace={"ml_prefilter": "bypassed_stale_model_policy"},
                )

            ml_score = scorer.score(flow_dict)
            ctx.ml_score = ml_score
            ml_threshold = system_settings.ml_prefilter_threshold

            if ml_score < ml_threshold:
                logger.info(
                    f"ML pre-filter: {candidate.ticker} rejected (score={ml_score:.2f} < {ml_threshold})",
                    extra={
                        "event": "ml_prefilter_skip",
                        "ticker": candidate.ticker,
                        "ml_score": ml_score,
                        "threshold": ml_threshold,
                    },
                )
                return StageResult(
                    action="SKIP",
                    reason=f"ML pre-filter: score {ml_score:.2f} below threshold ({ml_threshold})",
                    trace={
                        "ml_prefilter": True,
                        "ml_score": ml_score,
                        "threshold": ml_threshold,
                    },
                )

            return StageResult(
                action="CONTINUE",
                trace={"ml_score": ml_score, "threshold": ml_threshold},
            )

        except Exception as e:
            # Log but don't block on ML scorer failures — safety fallback
            logger.error(f"ML pre-filter failed for candidate: {e}, continuing with Solver evaluation", exc_info=True)
            return StageResult(
                action="CONTINUE",
                trace={"ml_prefilter_error": str(e)},
            )
