import json
import logging

from orion.core.logging_config import JSONFormatter


def test_json_formatter_structure():
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="/path/to/script.py",
        lineno=10,
        msg="Test message",
        args=(),
        exc_info=None,
    )

    # Simulate extra fields
    record.extra_field = "extra_value"

    output = formatter.format(record)
    data = json.loads(output)

    assert data["message"] == "Test message"
    assert data["level"] == "INFO"
    assert data["logger"] == "test_logger"
    assert data["extra_field"] == "extra_value"
    assert "timestamp" in data


def test_json_formatter_exception():
    formatter = JSONFormatter()
    try:
        raise ValueError("Oops")
    except ValueError:
        import sys

        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test_logger",
        level=logging.ERROR,
        pathname="script.py",
        lineno=20,
        msg="Error occurred",
        args=(),
        exc_info=exc_info,
    )

    output = formatter.format(record)
    data = json.loads(output)

    assert data["message"] == "Error occurred"
    assert "exc_info" in data
    assert "ValueError: Oops" in data["exc_info"]
