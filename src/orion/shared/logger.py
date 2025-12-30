import datetime
import json
import logging
import sys
import traceback
from typing import Any, Dict, Optional


class JSONFormatter(logging.Formatter):
    """
    Formatter that outputs JSON strings for all log records.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_record: Dict[str, Any] = {
            "ts": datetime.datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "service": record.name,
            "message": record.getMessage(),
            "file": record.filename,
            "line": record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
            log_record["stack_trace"] = traceback.format_exception(*record.exc_info)

        # Merge custom extras (anything not in standard LogRecord attributes)
        standard_attrs = set(logging.makeLogRecord({}).__dict__.keys())
        for k, v in record.__dict__.items():
            if k in standard_attrs:
                continue
            if k in log_record:
                continue
            log_record[k] = v

        # Ensure run_id is present when provided via env
        if "run_id" not in log_record:
            import os

            env_run_id = os.getenv("ORION_RUN_ID")
            if env_run_id:
                log_record["run_id"] = env_run_id

        # P2 Audit: Add correlation_id for request-level tracing
        if "correlation_id" not in log_record and "trace_id" in record.__dict__:
            log_record["correlation_id"] = record.__dict__["trace_id"]

        return json.dumps(log_record)


class HumanReadableFormatter(logging.Formatter):
    """
    Formatter that outputs human-readable logs for development.
    Palette's touch: Making logs a joy to read since 2024.
    """

    COLORS = {
        "DEBUG": "\033[90m",  # Gray
        "INFO": "\033[92m",  # Green
        "WARNING": "\033[93m",  # Yellow
        "ERROR": "\033[91m",  # Red
        "CRITICAL": "\033[1;91m",  # Bold Red
        "RESET": "\033[0m",
    }

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        color = self.COLORS.get(record.levelname, self.COLORS["RESET"])
        reset = self.COLORS["RESET"]

        # Basic message
        msg = f"{color}[{ts}] [{record.levelname:<5}] [{record.name}]{reset} {record.getMessage()}"

        # Extras
        standard_attrs = set(logging.makeLogRecord({}).__dict__.keys())
        extras = []
        for k, v in record.__dict__.items():
            if k in standard_attrs:
                continue
            # Skip run_id in dev mode if it's just clutter, but maybe keep it if needed.
            # Let's keep it but at the end.
            extras.append(f"{k}={v}")

        if extras:
            msg += f" \033[90m({', '.join(extras)})\033[0m"

        if record.exc_info:
            # Nice traceback
            tb = "".join(traceback.format_exception(*record.exc_info))
            msg += f"\n{color}{tb}{reset}"

        return msg


class _RunIdFilter(logging.Filter):
    def __init__(self, run_id: Optional[str]):
        super().__init__()
        self.run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        if self.run_id and not hasattr(record, "run_id"):
            record.run_id = self.run_id
        return True


def setup_struct_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Sets up a logger with JSONFormatter on StreamHandler (stdout).
    """
    import os
    import uuid

    run_id = os.getenv("ORION_RUN_ID")
    if not run_id:
        run_id = str(uuid.uuid4())
        os.environ["ORION_RUN_ID"] = run_id

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # clear existing handlers to avoid duplicates
    if logger.handlers:
        logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)

    # Check for human-readable mode
    log_format = os.getenv("ORION_LOG_FORMAT", "json").lower()
    if log_format in ("human", "dev", "text"):
        handler.setFormatter(HumanReadableFormatter())
    else:
        handler.setFormatter(JSONFormatter())

    logger.addHandler(handler)
    logger.addFilter(_RunIdFilter(run_id))

    # Propagate to root logger when running under pytest (caplog) or when explicitly enabled.
    # This keeps JSON output while allowing tests to capture logs deterministically.
    propagate = os.getenv("ORION_LOG_PROPAGATE") == "1" or ("pytest" in sys.modules)
    logger.propagate = propagate

    return logger
