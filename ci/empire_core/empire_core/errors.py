"""EmpireError — unified exception base for all Empire services.

Features:
- Structured to_dict() for API error responses
- Auto-logs error at WARNING level on construction (structlog)
- Accepts ErrorCode enum or plain string for code
- Carries details dict for context

Usage:
    from empire_core.errors import EmpireError, CommonErrorCode

    class CerberusError(EmpireError):
        pass

    raise CerberusError("Order failed", ErrorCode.ORDER_SUBMIT_FAILED, {"symbol": "AAPL"})
"""

from __future__ import annotations

from enum import Enum, StrEnum
from typing import Any

import structlog

_logger = structlog.get_logger("empire.errors")


class EmpireError(Exception):
    """Base exception for all Empire services."""

    def __init__(
        self,
        message: str,
        code: str | Enum = "UNKNOWN_ERROR",
        details: dict[str, Any] | None = None,
        *,
        log: bool = True,
    ):
        super().__init__(message)
        self.message = message
        self.code = str(code.value) if isinstance(code, Enum) else str(code)
        self.details = details or {}
        if log:
            _logger.warning(
                "empire_error",
                error_code=self.code,
                error_message=message,
                error_type=type(self).__name__,
                **self.details,
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to API error response format."""
        return {
            "error": True,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class CommonErrorCode(StrEnum):
    """Shared error codes across all Empire services.

    Each service extends with its own ErrorCode enum for service-specific codes.
    """

    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    TIMEOUT = "TIMEOUT"
    CONFIG_ERROR = "CONFIG_ERROR"
    DB_ERROR = "DB_ERROR"
    HTTP_ERROR = "HTTP_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
