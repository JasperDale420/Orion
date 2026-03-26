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
    setup_logging,
)

if TYPE_CHECKING:
    import structlog

_configured = False


def setup_struct_logger(name: str, level: int | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger. Backward-compatible with existing Orion code."""
    global _configured
    if not _configured:
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
