from typing import Any

from orion.core.solver_schema import SolverConfig
from orion.shared.logger import setup_struct_logger
from orion.storage.models_gold import CandidateTrade

logger = setup_struct_logger(__name__)

# Module-level cache for models loaded via file URI to avoid repeated disk I/O
_uri_model_cache: dict[str, Any] = {}


class SolverPipeline:
    """
    Executes a single Solver's decision pipeline for a given candidate.
    Encapsulates Rule Matching -> Feature Gen -> Model Inference -> Policy.
    """

    def __init__(self) -> None:
        # In a real implementation, we would load FeatureEngine and ModelRegistry here
        pass

    async def execute(
        self, solver: SolverConfig, candidate: CandidateTrade, feature_engine: Any | None = None
    ) -> tuple[float, float, dict[str, Any]]:
        """
        Runs the solver pipeline.

        Returns:
            (p_take, weight, trace)
            p_take: Probability to take the trade (0.0 to 1.0)
            weight: Solver's authority/confidence weight
            trace: Decision trace component
        """

        # 1. Rule Compatibility Check
        solver_rules = solver.rules if hasattr(solver, "rules") else []
        # Support both string list or object list if schema differs
        rule_ids = [r if isinstance(r, str) else r.id for r in solver_rules]

        if rule_ids and "*" not in rule_ids:
            if candidate.rule_id not in rule_ids:
                # Solver ignores this rule
                return (
                    0.0,
                    0.0,
                    {"reason": "Rule Mismatch", "solver_rules": rule_ids, "candidate_rule": candidate.rule_id},
                )

        # 2. Feature Generation
        # Use the injected engine or fallback
        from orion.processing.feature_engine import FeatureEngine

        if feature_engine is None:
            logger.warning("FeatureEngine not provided to SolverPipeline; state/history will be lost.")
            feature_engine = FeatureEngine()

        features = {}
        try:
            features = await feature_engine.compute(candidate, solver.universe)
        except Exception as e:
            from orion.core.errors import ErrorCode, FeatureComputationError

            logger.error(f"Feature computation failed for solver {solver.version_id}: {e}")
            raise FeatureComputationError(
                f"Feature Key Generation Failed: {e}", code=ErrorCode.FEATURE_COMPUTATION_FAILED
            ) from e

        # 3. Model Inference
        p_take = 0.0

        if solver.model and (solver.model.model_uri or solver.model.model_version):
            import pandas as pd

            from orion.ml.model_registry import get_model_registry

            registry = get_model_registry()
            model = None

            if solver.model.model_uri:
                # Direct file path — load with joblib (handle file:// URIs)
                import joblib

                raw_uri = solver.model.model_uri
                path_str = raw_uri.removeprefix("file://") if raw_uri.startswith("file://") else raw_uri
                if path_str in _uri_model_cache:
                    model = _uri_model_cache[path_str]
                else:
                    try:
                        model = joblib.load(path_str)
                        _uri_model_cache[path_str] = model
                    except FileNotFoundError:
                        model = None

            # Fall through to registry lookup if URI load failed or no URI
            if model is None and solver.model.model_version:
                model = registry.get_active_model(solver.model.model_version)

            uri = solver.model.model_uri or solver.model.model_version or "unknown"

            if model:
                try:
                    # Convert features to DataFrame for potential named-column support
                    df_feats = pd.DataFrame([features])

                    # Assume classifier with predict_proba
                    if hasattr(model, "predict_proba"):
                        # [0] is prob class 0, [1] is prob class 1 (Trade)
                        p_take = float(model.predict_proba(df_feats)[0][1])
                    elif hasattr(model, "predict"):
                        p_take = float(model.predict(df_feats)[0])
                    else:
                        from orion.core.errors import ErrorCode, ModelInferenceError

                        raise ModelInferenceError(
                            f"Model {uri} has no predict method", code=ErrorCode.MODEL_INFERENCE_FAILED
                        )
                except Exception as e:
                    from orion.core.errors import ErrorCode, ModelInferenceError

                    # CRITICAL FIX: Fail Fast. Do not fallback.
                    logger.error(f"Model inference failed for {uri}: {e}")
                    raise ModelInferenceError(f"Inference Failed: {e}", code=ErrorCode.MODEL_INFERENCE_FAILED) from e
            else:
                from orion.core.errors import ErrorCode, ModelInferenceError

                raise ModelInferenceError(f"Model {uri} not found in registry", code=ErrorCode.MODEL_INFERENCE_FAILED)
        else:
            # Rule-only solver: Trust the candidate confidence
            p_take = candidate.confidence if candidate.confidence else 0.8

        # Penalize high vol (Global Dynamic Threshold)
        vol_thresh = solver.volatility_penalty_threshold if solver.volatility_penalty_threshold is not None else 0.02
        if features.get("session_volatility", 0) > vol_thresh:
            p_take *= 0.8

        # 4. Weighting
        # Check if solver has specific weight for this rule
        weight = 1.0
        if hasattr(solver, "entry_logic") and solver.entry_logic:
            # Basic logic to find weight?
            pass

        trace = {
            "stage": "model_inference_deterministic",
            "model_version": (
                solver.model.model_version
                if (solver.model and hasattr(solver.model, "model_version"))
                else "rules_only"
            ),
            "p_take_raw": p_take,
            "features_used_count": len(features),
            "feature_keys": list(features.keys()),
        }

        return p_take, weight, trace
