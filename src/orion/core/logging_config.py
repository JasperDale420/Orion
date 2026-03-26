"""Structured logging configuration for Orion using structlog.

Provides `setup_logging()` for modules that configure the root logger at startup.
Delegates to the shared structlog configuration in `orion.shared.logger`.
"""

from orion.shared.logger import setup_struct_logger


def setup_logging(level: str = "INFO") -> None:
    """Configure structlog-based JSON logging for the process.

    Modules that call this at startup get consistent JSON output.
    """
    # Calling setup_struct_logger triggers empire_core setup once
    setup_struct_logger("orion", level=None)
