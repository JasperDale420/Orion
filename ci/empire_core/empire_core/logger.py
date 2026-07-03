"""Unified structured logging for all Empire services.

Usage:
    from empire_core.logger import setup_logging, get_logger

    # Call once at service startup
    setup_logging("cerberus")

    # In any module
    logger = get_logger(__name__)
    logger.info("order_placed", symbol="AAPL", qty=100)

    # Bind trace/correlation IDs
    from empire_core.logger import bind_context, clear_context
    bind_context(trace_id="req-abc-123", correlation_id="corr-456")
    logger.info("processing")  # trace_id and correlation_id auto-injected
    clear_context()

    # Structured error helpers
    from empire_core.logger import log_error
    log_error(logger, exc, "bronze_write", feed="quotes", symbol="AAPL")
"""

from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import structlog

_configured = False
_service_name: str = "unknown"


class _StdoutProxy:
    """Proxy that always writes to the current ``sys.stdout``.

    ``logging.StreamHandler(sys.stdout)`` captures the *reference* to
    ``sys.stdout`` at handler-creation time.  If the handler is created
    during pytest collection (when pytest has already redirected stdout),
    the handler is forever locked to the stale fd and ``capsys`` /
    ``capfd`` can never intercept the output.

    This proxy solves the problem by delegating every write to whatever
    ``sys.stdout`` points to *right now*, so test-capture fixtures work
    correctly regardless of import order.
    """

    @staticmethod
    def write(s: str) -> int:
        return sys.stdout.write(s)

    @staticmethod
    def flush() -> None:
        sys.stdout.flush()


class _StructlogMessageFormatter(logging.Formatter):
    """Avoid appending a second raw traceback after structlog JSON records."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        suppress_stdlib_traceback = (
            record.exc_info is not None
            and message.lstrip().startswith("{")
            and '"exception":' in message
        )
        if not suppress_stdlib_traceback:
            return super().format(record)

        exc_info = record.exc_info
        exc_text = record.exc_text
        record.exc_info = None
        record.exc_text = None
        try:
            return super().format(record)
        finally:
            record.exc_info = exc_info
            record.exc_text = exc_text


# ---------------------------------------------------------------------------
# Structlog processors
# ---------------------------------------------------------------------------


def _upcase_level(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Uppercase the log level (e.g. 'info' -> 'INFO')."""
    if "level" in event_dict:
        event_dict["level"] = event_dict["level"].upper()
    return event_dict


def _rename_event_to_message(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Rename structlog's 'event' key to 'message' for consistency."""
    if "event" in event_dict:
        event_dict["message"] = event_dict.pop("event")
    return event_dict


def _inject_service_name(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Inject service_name into every log entry."""
    if "service" not in event_dict:
        event_dict["service"] = _service_name
    return event_dict


# ---------------------------------------------------------------------------
# File handlers
# ---------------------------------------------------------------------------


def _daily_namer(default_name: str) -> str:
    """Rename rotated log files to a ``{stem}_{YYYY-MM-DD}.log`` form.

    ``TimedRotatingFileHandler`` appends ``.YYYY-MM-DD`` to the active
    filename when it rotates. We want the rotated-out file to be named
    e.g. ``logs/3roses_2026-03-11.log`` (date-in-name, not suffix-date)
    so operators can sort and tail dated files easily.

    Transforms:

    * ``logs/3roses.log.2026-03-11``           → ``logs/3roses_2026-03-11.log``
      (new shape — active file has no date in its name)
    * ``logs/3roses_2026-03-12.log.2026-03-11`` → ``logs/3roses_2026-03-11.log``
      (legacy shape — active file already had a date; date in stem is
      replaced with the rotated date)

    Anything that does not match the trailing ``.YYYY-MM-DD`` pattern is
    returned unchanged.

    **Collision handling (upgrade path):** when upgrading from the
    pre-fix code, log directories already contain ``{service}_{date}.log``
    files written by the buggy old setup. ``TimedRotatingFileHandler.
    doRollover()`` calls ``os.rename(src, dest)`` and bails out if
    ``dest`` exists, which would leave the active file unrotated and
    accumulating forever. To preserve forward progress AND not clobber
    the legacy archive, we append a ``.N`` sequence suffix on collision:
    ``{service}_{date}.log.1``, ``.2``, etc. until a free name is found.
    Operators can manually merge the sequenced files into the legacy
    archive at their leisure; rollover never blocks.
    """
    match = re.search(r"\.(\d{4}-\d{2}-\d{2})$", default_name)
    if not match:
        return default_name
    date_suffix = match.group(1)
    base = default_name[: match.start()]
    # Legacy shape: base already contains a date in the stem
    # (e.g. "logs/3roses_2026-03-12.log") — replace it with the
    # rotated date so the file is named for the day it covers.
    stem_match = re.search(r"_\d{4}-\d{2}-\d{2}\.log$", base)
    if stem_match:
        prefix = base[: stem_match.start()]
        candidate = f"{prefix}_{date_suffix}.log"
    elif base.endswith(".log"):
        # New shape: base is the active filename without a date
        # (e.g. "logs/3roses.log"). Insert the rotated date before the
        # ``.log`` extension so the rotated file becomes
        # ``logs/3roses_2026-03-11.log``.
        candidate = f"{base[:-4]}_{date_suffix}.log"
    else:
        return default_name
    # Collision-safe: if a legacy file already occupies the target name,
    # append .1, .2, ... until a free name is found. Rollover then
    # always succeeds. Capped at 1000 attempts to avoid infinite loops
    # in pathological cases.
    if not os.path.exists(candidate):
        return candidate
    for seq in range(1, 1000):
        sequenced = f"{candidate}.{seq}"
        if not os.path.exists(sequenced):
            return sequenced
    # Fallthrough: 1000 collisions on the same target is implausible;
    # return the highest sequence and let doRollover fail loudly if the
    # cap is somehow reached.
    return f"{candidate}.999"


def _make_daily_file_handler(
    log_dir: Path, service_name: str, backup_count: int, *, level: int = logging.DEBUG
) -> logging.Handler:
    """Create a file handler for all log levels with daily rotation.

    The active (currently-written) file is ``{service}.log`` — note the
    bare basename with NO date. When ``TimedRotatingFileHandler`` rotates
    at midnight, the rotated-out file is renamed via ``_daily_namer`` to
    ``{service}_{YYYY-MM-DD}.log`` (the date the rotated file covers) and
    a fresh ``{service}.log`` is opened.

    This naming was changed in 2026-05 to fix a long-running-process bug:
    the previous implementation baked ``date.today()`` into the active
    filename at process start, so a process running past midnight kept
    writing to the original (now misnamed) file forever. The bare basename
    side-steps that entirely — the active file always has a stable name
    and only rotated files carry dates.

    Operators tail / log-ship ``{log_dir}/{service}.log`` for the live
    stream; dated files in the same directory are the historical archive
    (capped at ``backup_count`` days, default 14).
    """
    from logging.handlers import TimedRotatingFileHandler

    filename = log_dir / f"{service_name}.log"

    handler = TimedRotatingFileHandler(
        filename=filename,
        when="midnight",
        interval=1,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.namer = _daily_namer
    handler.setLevel(level)
    return handler


def _make_daily_error_handler(
    log_dir: Path, service_name: str, backup_count: int
) -> logging.Handler:
    """Create a file handler for WARNING+ logs only with daily rotation.

    Active file is ``{service}_errors.log`` (bare basename). On midnight
    rotation, the rotated-out file becomes
    ``{service}_errors_{YYYY-MM-DD}.log`` via ``_daily_namer``. See
    :func:`_make_daily_file_handler` for the rationale behind the
    date-less active filename.
    """
    from logging.handlers import TimedRotatingFileHandler

    filename = log_dir / f"{service_name}_errors.log"

    handler = TimedRotatingFileHandler(
        filename=filename,
        when="midnight",
        interval=1,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.namer = _daily_namer
    handler.setLevel(logging.WARNING)
    return handler


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def setup_logging(
    service_name: str,
    level: str = "INFO",
    *,
    log_file: bool = True,
    error_log: bool = True,
    log_dir: str | None = None,
    backup_count: int = 14,
    force: bool = False,
) -> None:
    """Configure structlog and stdlib logging for a service.

    Args:
        service_name: Name of the service (used in log filenames and auto-injected).
        level: Log level string (DEBUG, INFO, WARNING, ERROR).
        log_file: Whether to write logs to a daily rotating file.
        error_log: Whether to write a separate WARNING+ error log file.
        log_dir: Directory for log files. Defaults to EMPIRE_LOG_DIR env or "./logs".
        backup_count: Number of rotated log files to keep (default 14 days).
        force: Force reconfiguration (used in tests).
    """
    global _configured, _service_name
    force = force or "PYTEST_CURRENT_TEST" in os.environ
    if _configured and not force:
        return

    _service_name = service_name

    # Allow env var overrides
    level = os.environ.get("EMPIRE_LOG_LEVEL", level).upper()
    log_format = os.environ.get("EMPIRE_LOG_FORMAT", "json").lower()
    use_json = log_format not in ("human", "dev", "text")

    # EMPIRE_LOG_BACKUP_COUNT overrides the default 14-day daily-log
    # retention. Services with verbose output or longer post-mortem
    # windows (e.g. Data-Gateway) can bump this without touching code.
    env_backup = os.environ.get("EMPIRE_LOG_BACKUP_COUNT")
    if env_backup is not None:
        try:
            parsed = int(env_backup)
            if parsed > 0:
                backup_count = parsed
        except ValueError:
            # Fall through to the caller-provided default; warn once.
            logging.getLogger(__name__).warning(
                "Invalid EMPIRE_LOG_BACKUP_COUNT=%r; using default %d",
                env_backup,
                backup_count,
            )

    # Choose output renderer
    if use_json:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            _upcase_level,
            _inject_service_name,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _rename_event_to_message,
            renderer,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    handlers: list[logging.Handler] = [logging.StreamHandler(_StdoutProxy())]

    if log_file:
        resolved_dir = log_dir or os.environ.get("EMPIRE_LOG_DIR", "logs")
        path = Path(resolved_dir)
        path.mkdir(parents=True, exist_ok=True)
        handlers.append(
            _make_daily_file_handler(path, service_name, backup_count)
        )
        if error_log:
            handlers.append(
                _make_daily_error_handler(path, service_name, backup_count)
            )

    for handler in handlers:
        handler.setFormatter(_StructlogMessageFormatter("%(message)s"))

    logging.basicConfig(
        format="%(message)s",
        handlers=handlers,
        level=getattr(logging, level, logging.INFO),
        force=force,
    )

    # Suppress noisy HTTP library loggers
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structured logger bound to the given name."""
    return structlog.get_logger(name)


# ---------------------------------------------------------------------------
# Context helpers (trace_id, correlation_id, etc.)
# ---------------------------------------------------------------------------


def bind_context(**kwargs: Any) -> None:
    """Bind key-value pairs to structlog's contextvars.

    All subsequent log calls in the same async/thread context will include
    these fields automatically. Common fields: trace_id, correlation_id, run_id.

    Example::

        bind_context(trace_id="req-abc-123")
        logger.info("processing")  # includes trace_id automatically
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    """Clear all bound context variables."""
    structlog.contextvars.clear_contextvars()


def unbind_context(*keys: str) -> None:
    """Remove specific keys from the bound context."""
    structlog.contextvars.unbind_contextvars(*keys)


# ---------------------------------------------------------------------------
# Structured error helpers
# ---------------------------------------------------------------------------


def log_error(
    logger: structlog.stdlib.BoundLogger,
    error: Exception,
    operation: str,
    **context: Any,
) -> None:
    """Log an error with structured context and full traceback.

    Automatically extracts error_type and error_message. If the exception
    is an EmpireError, also extracts error_code and details.

    Args:
        logger: The structlog logger instance.
        error: The exception to log.
        operation: What operation failed (e.g. "bronze_write", "order_submit").
        **context: Additional structured fields (symbol, feed, order_id, etc.).
    """
    fields: dict[str, Any] = {
        "operation": operation,
        "error_type": type(error).__name__,
        "error_message": str(error),
        **context,
    }

    # Extract EmpireError fields if available
    if hasattr(error, "code"):
        fields["error_code"] = error.code
    if hasattr(error, "details") and error.details:
        fields["error_details"] = error.details

    logger.error("error", exc_info=True, **fields)


def log_retry(
    logger: structlog.stdlib.BoundLogger,
    operation: str,
    attempt: int,
    max_retries: int,
    delay_seconds: float,
    error: str,
) -> None:
    """Log a retry attempt."""
    logger.warning(
        "retry",
        operation=operation,
        attempt=attempt,
        max_retries=max_retries,
        delay_seconds=delay_seconds,
        error=error,
    )
