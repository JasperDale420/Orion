"""Structured logging configuration for Orion using structlog.

Provides `setup_logging()` for modules that configure the root logger at startup.
Delegates to the shared structlog configuration in `orion.shared.logger`.
"""

import logging

from orion.shared.logger import _configure


def setup_logging(level: str = "INFO") -> None:
    """Configure structlog-based JSON logging for the process.

    Modules that call this at startup get consistent JSON output.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    _configure(numeric_level)
