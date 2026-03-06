"""Structured logging for Orion modules using structlog.

Provides `setup_struct_logger(name)` which returns a structlog-backed logger.
Features:
  - JSON output by default, human-readable when ORION_LOG_FORMAT=human
  - Auto-injected `run_id` from ORION_RUN_ID env var (or generated UUID)
  - Uppercase level names for consistency
  - Compatible with pytest caplog
  - Captures standard library `logging` via ProcessorFormatter
  - Logs both to console and a rotating file (`logs/orion.log`)
"""

import logging
import logging.handlers
import os
import sys
import uuid
from typing import Any

import structlog


def _upcase_level(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Uppercase the log level (e.g. 'info' → 'INFO')."""
    if "level" in event_dict:
        event_dict["level"] = event_dict["level"].upper()
    return event_dict


def _rename_event_to_message(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Rename structlog's 'event' key to 'message' for backward compatibility."""
    if "event" in event_dict:
        event_dict["message"] = event_dict.pop("event")
    return event_dict


def _inject_run_id(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Inject ORION_RUN_ID into every log entry."""
    run_id = os.getenv("ORION_RUN_ID")
    if run_id and "run_id" not in event_dict:
        event_dict["run_id"] = run_id
    return event_dict


_configured = False


def _configure(level: int = logging.INFO) -> None:
    """One-time structlog and stdlib logging configuration for the process."""
    global _configured
    if _configured:
        return
    _configured = True

    # Ensure ORION_RUN_ID is set
    if not os.getenv("ORION_RUN_ID"):
        os.environ["ORION_RUN_ID"] = str(uuid.uuid4())

    log_format = os.getenv("ORION_LOG_FORMAT", "json").lower()
    use_json = log_format not in ("human", "dev", "text")

    # ----- 1. Shared Structlog Processors -----
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _upcase_level,
        _inject_run_id,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.format_exc_info,
        _rename_event_to_message,
        # To pass dicts to stdlib's formatting correctly
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ]

    # ----- 2. Configure Structlog to bridge to stdlib -----
    structlog.configure(
        processors=shared_processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging via foreign_pre_chain
    foreign_processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.ExtraAdder(),
        _upcase_level,
        _inject_run_id,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
    ]

    # Only keep the output processors for the handlers
    if use_json:
        console_formatter = structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
            foreign_pre_chain=foreign_processors,
        )
    else:
        console_formatter = structlog.stdlib.ProcessorFormatter(
            processor=structlog.dev.ConsoleRenderer(),
            foreign_pre_chain=foreign_processors,
        )

    # The file logger should ALWAYS be JSON for machine readability
    file_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=foreign_processors,
    )

    # ----- 4. Handlers -----
    # Console
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(level)

    # Rotating File
    log_dir = os.environ.get("ORION_LOG_DIR", os.path.join(os.getcwd(), "logs"))
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "orion.log")

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB per file
        backupCount=5,  # Keep 5 rotating backups
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(level)

    # ----- 5. Root Logger Registration -----
    root_logger = logging.getLogger()
    # Remove default handlers if any
    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.setLevel(level)

    # Silence noise from third-party libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    # Configure structure to run foreign processors on stdlib logs
    structlog.stdlib.ProcessorFormatter.custom_processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _upcase_level,
        _inject_run_id,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
        _rename_event_to_message,
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
    ]


def setup_struct_logger(name: str, level: int = logging.INFO) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger with JSON output and run_id injection.

    Callers continue to use logger.info(), logger.error(), etc.
    Even standard python logging.getLogger() calls will be captured.
    """
    _configure(level)
    return structlog.get_logger(name)
