"""Structured logging for Orion modules using structlog.

Provides `setup_struct_logger(name)` which returns a structlog-backed logger.
Features:
  - JSON output by default, human-readable when ORION_LOG_FORMAT=human
  - Auto-injected `run_id` from ORION_RUN_ID env var (or generated UUID)
  - Uppercase level names for consistency
  - Compatible with pytest caplog
"""

import logging
import os
import sys
import uuid
from typing import Any

import structlog


def _upcase_level(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Uppercase the log level (e.g. 'info' → 'INFO')."""
    if "level" in event_dict:
        event_dict["level"] = event_dict["level"].upper()
    return event_dict


def _rename_event_to_message(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Rename structlog's 'event' key to 'message' for backward compatibility."""
    if "event" in event_dict:
        event_dict["message"] = event_dict.pop("event")
    return event_dict


def _inject_run_id(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Inject ORION_RUN_ID into every log entry."""
    run_id = os.getenv("ORION_RUN_ID")
    if run_id and "run_id" not in event_dict:
        event_dict["run_id"] = run_id
    return event_dict


_configured = False


def _configure(level: int = logging.INFO) -> None:
    """One-time structlog configuration for the process."""
    global _configured
    if _configured:
        return
    _configured = True

    # Ensure ORION_RUN_ID is set
    if not os.getenv("ORION_RUN_ID"):
        os.environ["ORION_RUN_ID"] = str(uuid.uuid4())

    log_format = os.getenv("ORION_LOG_FORMAT", "json").lower()
    use_json = log_format not in ("human", "dev", "text")

    if use_json:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            _upcase_level,
            _inject_run_id,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _rename_event_to_message,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )

    # Configure stdlib logging for third-party library output
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def setup_struct_logger(name: str, level: int = logging.INFO) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger with JSON output and run_id injection.

    Drop-in replacement for the previous stdlib-based setup_struct_logger.
    Callers continue to use logger.info(), logger.error(), etc.
    """
    _configure(level)
    return structlog.get_logger(name)
