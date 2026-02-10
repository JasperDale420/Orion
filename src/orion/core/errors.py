from enum import Enum
from typing import Any, Dict, Optional


class ErrorCode(Enum):
    # Provider/Ingestion Errors
    PROVIDER_AUTH_FAILED = "PROVIDER_AUTH_FAILED"
    PROVIDER_RATE_LIMIT = "PROVIDER_RATE_LIMIT"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_SCHEMA_DRIFT = "PROVIDER_SCHEMA_DRIFT"
    DESERIALIZATION_ERROR = "DESERIALIZATION_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"

    # Storage Errors
    HOTSTORE_WRITE_FAILED = "HOTSTORE_WRITE_FAILED"
    LAKE_WRITE_FAILED = "LAKE_WRITE_FAILED"
    VECTOR_WRITE_FAILED = "VECTOR_WRITE_FAILED"
    DUPLICATE_EVENT = "DUPLICATE_EVENT"

    # Execution/Risk Errors
    EXECUTION_FAILED = "EXECUTION_FAILED"
    RISK_CHECK_FAILED = "RISK_CHECK_FAILED"
    CIRCUIT_BREAKER_TRIPPED = "CIRCUIT_BREAKER_TRIPPED"
    SOLVER_SELECTION_FAILED = "SOLVER_SELECTION_FAILED"
    SOLVER_EVAL_FAILED = "SOLVER_EVAL_FAILED"
    MODEL_INFERENCE_FAILED = "MODEL_INFERENCE_FAILED"
    FEATURE_COMPUTATION_FAILED = "FEATURE_COMPUTATION_FAILED"
    ROUTER_NO_ELIGIBLE_SOLVER = "ROUTER_NO_ELIGIBLE_SOLVER"
    SOLVER_DSL_VALIDATION_FAILED = "SOLVER_DSL_VALIDATION_FAILED"
    META_EDIT_INVALID = "META_EDIT_INVALID"
    META_METRICS_INCONSISTENT = "META_METRICS_INCONSISTENT"

    # System Errors
    BACKPRESSURE = "BACKPRESSURE"
    CLOCK_SKEW_DETECTED = "CLOCK_SKEW_DETECTED"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


class OrionError(Exception):
    """Base exception for all Orion errors."""

    def __init__(
        self, message: str, code: ErrorCode = ErrorCode.UNKNOWN_ERROR, context: Optional[Dict[str, Any]] = None
    ):
        super().__init__(message)
        self.message = message
        # Preserve ErrorCode enum compatibility used across runtime and tests.
        self.code = code if isinstance(code, ErrorCode) else ErrorCode(str(code))
        self.context = context or {}
        # Alias for newer envelope helpers.
        self.details = self.context

    def to_dict(self) -> Dict[str, Any]:
        """Convert to standard Empire error envelope."""
        return {
            "error": True,
            "code": self.code.value,
            "message": self.message,
            "details": self.details,
        }


class ProviderError(OrionError):
    """Errors related to external data providers (UW, Alpaca)."""

    pass


class StorageError(OrionError):
    """Errors related to database or lakehouse operations."""

    pass


class ExecutionError(OrionError):
    """Errors related to trade execution."""

    pass


class ModelInferenceError(OrionError):
    """Errors during model prediction."""

    pass


class FeatureComputationError(OrionError):
    """Errors during feature generation."""

    pass
