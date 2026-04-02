"""Structured logging for Orion — delegates to empire_core.logger.

Preserves setup_struct_logger(name) API for backward compatibility.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

from empire_core.logger import (
    bind_context,
    get_logger,
    setup_logging as _setup_logging_impl,
)

if TYPE_CHECKING:
    import structlog

_configured = False


def setup_logging(
    service_name: str = "orion",
    level: str = "INFO",
    *,
    log_file: bool = True,
    error_log: bool = True,
    log_dir: str | None = None,
    backup_count: int = 14,
    force: bool = False,
) -> None:
    """Backward-compatible wrapper around empire_core logging setup."""
    _setup_logging_impl(
        service_name,
        level=level,
        log_file=log_file,
        error_log=error_log,
        log_dir=log_dir,
        backup_count=backup_count,
        force=force,
    )


def setup_struct_logger(name: str, level: int | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger. Backward-compatible with existing Orion code."""
    global _configured
    in_pytest = "PYTEST_CURRENT_TEST" in os.environ
    if not _configured or in_pytest:
        # Map Orion-specific env vars to empire standard
        if os.getenv("ORION_LOG_FORMAT") and not os.getenv("EMPIRE_LOG_FORMAT"):
            os.environ["EMPIRE_LOG_FORMAT"] = os.getenv("ORION_LOG_FORMAT", "json")
        if os.getenv("ORION_LOG_DIR") and not os.getenv("EMPIRE_LOG_DIR"):
            os.environ["EMPIRE_LOG_DIR"] = os.getenv("ORION_LOG_DIR", "logs")

        setup_logging("orion")

        # Inject run_id into context
        run_id = os.getenv("ORION_RUN_ID")
        if not run_id:
            run_id = str(uuid.uuid4())
            os.environ["ORION_RUN_ID"] = run_id
        bind_context(run_id=run_id)

        _configured = True

    return get_logger(name)
