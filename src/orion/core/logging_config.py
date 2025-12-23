import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict


class JSONFormatter(logging.Formatter):
    """
    Formatter that outputs JSON strings with structured fields.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_record: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "path": record.pathname,
            "line": record.lineno,
            "func": record.funcName,
        }

        # Merge 'extra' fields
        if hasattr(record, "extra"):
            log_record.update(record.extra)
        elif hasattr(record, "__dict__"):
            # Some loggers might inject directly into __dict__, be careful not to grab everything
            # Standard 'extra' approach in python logging puts keys in record.__dict__
            # We filter out standard LogRecord attributes to find custom extras
            standard_attrs = set(logging.makeLogRecord({}).__dict__.keys())
            extras = {k: v for k, v in record.__dict__.items() if k not in standard_attrs and k not in log_record}
            log_record.update(extras)

        # Handle exception info
        if record.exc_info:
            log_record["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(log_record)


def setup_logging(level: str = "INFO"):
    """
    Configures the root logger to use JSON formatting.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root_logger.addHandler(handler)

    # Silence noisy libs
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


# Helper for call sites to ensure dicts are passed correctly if they don't use 'extra=' kwarg consistently
# (Optional usage)
